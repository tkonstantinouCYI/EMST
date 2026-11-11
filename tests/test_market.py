import unittest
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from dataclasses import replace
import pulp as lp
from emst.calendar import day_intervals,next_business_day,historical_days
from emst.model import Order,Forecast,PRODUCTS,Registration
from emst.commercial import clear_dam,forward_schedules
from emst.optimization import solve,dual_face,freeze_discrete
from emst.settlement import (production_bridge,offtake_bridge,instructed_cash,
    period_price,Ledger,assess_penalty,reserve_cash,statement,D,settle_energy)
from emst.clearing import required_margin,default_waterfall,late_payment_charge,invoice,pay,ClearingCycle
from emst.scenario import synthetic_day
from emst.metering import Meter,allocate_profile,storage_delivery,eligible_fee_base,validate_rtbm_eligibility
from emst.isp import schedule
from emst.rtbm import clear_rtu


class CalendarTests(unittest.TestCase):
    def test_ordinary_and_DST(self):
        for d,n in ((date(2026,11,5),48),(date(2026,3,29),46),(date(2026,10,25),50)):
            self.assertEqual(len(day_intervals(d,30)),n)
            rtus=day_intervals(d,5)
            self.assertEqual(len(rtus),n*6)
            self.assertEqual(len({r.start for r in rtus}),n*6)
            self.assertEqual(len({r.id for r in rtus}),n*6)
        with self.assertRaises(ValueError):day_intervals(date(2026,10,25),30,"verified_rules_only")

    def test_holiday_and_history(self):
        self.assertEqual(next_business_day(date(2026,11,6),{date(2026,11,9)}),date(2026,11,10))
        with self.assertRaises(ValueError):historical_days(date(2026,11,5),{date(2026,11,5):True})

    def test_forecast_causality(self):
        now=datetime.now(timezone.utc)
        f=Forecast(now,now+timedelta(hours=1),(1.,),"future")
        with self.assertRaises(ValueError):f.at_cutoff(now)


class CommercialTests(unittest.TestCase):
    def test_simple_market(self):
        orders=[Order("s1","g","g",0,"sell",10,40),Order("s2","h","h",0,"sell",10,80),
                Order("b","l","l",0,"buy",15,200)]
        r=clear_dam(orders,1)
        self.assertAlmostEqual(r["accepted_mwh"]["s2"],5)
        self.assertAlmostEqual(r["prices_eur_mwh"][0],80)
        self.assertAlmostEqual(r["welfare_eur"],2200)

    def test_linked_block_and_exclusive(self):
        orders=[Order("a","g","g",0,"sell",5,40,"A",1),
                Order("child","g","g",1,"sell",5,40,"B",1,"A"),
                Order("alt","g","g",0,"sell",5,70,"C",1,None,"choice"),
                Order("alt2","g","g",0,"sell",5,30,"D",1,None,"choice"),
                Order("buy0","l","l",0,"buy",10,200),Order("buy1","l","l",1,"buy",5,200)]
        r=clear_dam(orders,2)
        self.assertEqual(r["blocks"]["A"],1)
        self.assertEqual(r["blocks"]["B"],1)
        self.assertEqual(r["blocks"]["D"],1)
        self.assertEqual(r["blocks"]["C"],0)

    def test_notification_validation(self):
        s=synthetic_day()
        u={u.id:u for u in s["units"]}
        schedules,known=forward_schedules(s["forwards"],s["registrations"],u,48)
        self.assertTrue(known)
        self.assertEqual(schedules["thermal_A"][0],25)
        with self.assertRaises(ValueError):
            forward_schedules([replace(s["forwards"][0],seller="other")],s["registrations"],u,48)
        _,known=forward_schedules([replace(s["forwards"][0],price_eur_mwh=None)],s["registrations"],u,48)
        self.assertFalse(known)


class PricingTests(unittest.TestCase):
    def test_reserve_quote_and_dual(self):
        m=lp.LpProblem("reserve",lp.LpMinimize)
        a=lp.LpVariable("a",0,10);b=lp.LpVariable("b",0,20)
        m+=4*a+9*b
        m+=a+b>=20,"requirement"
        self.assertAlmostEqual(solve(m),130)
        p,r=dual_face(m,["requirement"])
        self.assertEqual(p["requirement"],9)
        self.assertEqual(r["requirement"],[9,9])
        self.assertEqual(20*p["requirement"],180)

    def test_nonunique_common_dual_face(self):
        m=lp.LpProblem("kink",lp.LpMinimize)
        a=lp.LpVariable("a",0,10);b=lp.LpVariable("b",0,20)
        m+=a+2*b;m+=a+b>=10,"req"
        solve(m)
        p,r=dual_face(m,["req"])
        self.assertEqual(r["req"],[1,2])
        self.assertEqual(p["req"],1)

    def test_nonconvex_frozen_price(self):
        m=lp.LpProblem("start",lp.LpMinimize)
        a=lp.LpVariable("a",0,10);b=lp.LpVariable("b",0,20)
        on=lp.LpVariable("on",cat="Binary")
        m+=5*a+8*b+100*on;m+=b<=20*on;m+=a+b>=11,"req"
        m.constraints["req"].changeRHS(10)
        ten_mw=solve(m)
        self.assertAlmostEqual(ten_mw,50)
        m.constraints["req"].changeRHS(11)
        self.assertAlmostEqual(solve(m),158)
        self.assertAlmostEqual(lp.value(m.objective)-ten_mw,108)
        self.assertTrue(freeze_discrete(m))
        solve(m,False)
        p,_=dual_face(m,["req"])
        self.assertAlmostEqual(p["req"],8,places=6)

    def test_shutdown_transition_energy_is_not_lost(self):
        s=synthetic_day();u=replace(s["units"][0],initial_mw=20)
        result=schedule([u],s["periods"][:1],[10],[0],{u.id:[10]},
            {p:[0] for p in PRODUCTS},{u.id:dict(on=1,end_mw=20,duration_periods=10)},
            s["initial_cutoff"],"shutdown_test",False)
        row=result["rows"][0]
        self.assertEqual(row["phase"],"desync")
        self.assertEqual(row["phase_binaries"]["desync"],1)
        self.assertEqual(row["on"],1)
        self.assertAlmostEqual(row["power_mw"],10)
        self.assertAlmostEqual(row["end_mw"],0)


class SettlementTests(unittest.TestCase):
    def test_tolerance_and_AGC(self):
        expected=[(41.5,1.5,0),(41,1.16,-.16),(43,2.84,.16)]
        for meter,inst,uninst in expected:
            b=production_bridge(40,2,0,meter,.02)
            self.assertAlmostEqual(b["INSTQ"],inst)
            self.assertAlmostEqual(b["UNINSTQ"],uninst)
        b=production_bridge(40,2,0,41,.02,agc=True)
        self.assertEqual(b["INSTQ"],1);self.assertEqual(b["UNINSTQ"],0)
        self.assertEqual(production_bridge(0,0,0,1,.02)["UNINSTQ"],1)

    def test_offtake_signs(self):
        self.assertEqual(offtake_bridge(40,41)["UNINSTQ"],-1)
        self.assertEqual(offtake_bridge(40,39)["UNINSTQ"],1)
        self.assertEqual(offtake_bridge(40,41,tolerance=.03)["adjusted_MSQ"],40)
        self.assertEqual(offtake_bridge(40,45,tolerance=.03)["adjusted_MSQ"],41.2)

    def test_four_payment_directions(self):
        for system,q,expected in (("short",2,300),("short",-2,-120),("long",2,360),("long",-2,-140)):
            self.assertEqual(instructed_cash(q,system,150,70,180,60),expected)
        self.assertEqual(instructed_cash(-2,"short",150,70,180,-20),40)

    def test_weighted_and_historical_price(self):
        def r(up,down,price,forming=True):
            return {"price_eur_mwh":price,"rows":[dict(up_mwh=up,down_mwh=down,price_forming=forming,reason="ordinary_market")]}
        history=[[80]*6,[100]*6,[120]*6]
        p=period_price([r(10,8,100),r(4,0,200)],history)
        self.assertAlmostEqual(p["price_eur_mwh"],1000/6)
        self.assertAlmostEqual(p["gross_sensitivity_eur_mwh"],2600/22)
        p=period_price([r(0,0,1000)],history)
        self.assertEqual(p["price_eur_mwh"],100)
        p=period_price([r(1,0,300,False)],history)
        self.assertEqual(p["price_eur_mwh"],100)
        with self.assertRaises(ValueError):period_price([],[])

    def test_reserve_partial_duration_no_double_dt(self):
        s=synthetic_day();u=s["units"][0]
        row=dict(entity=u.id,period=0,reserve_mw={p:10 for p in PRODUCTS},reserve_price_eur_mw_period={p:9 for p in PRODUCTS})
        ledger=Ledger()
        reserve_cash(ledger,[row],{(u.id,0,p):.5 for p in PRODUCTS},s["periods"],{u.id:u})
        self.assertEqual(ledger.balances()[f"participant:{u.participant}:nonDAM"],D(315))

    def test_ledger_and_revision(self):
        l=Ledger();l.post("p","S04","producer","A-B",1,100,"day","r","m1")
        self.assertEqual(l.revise("p",2,70,"m2"),D(-30))
        self.assertEqual(l.revise("p",2,70,"m2"),0)
        self.assertEqual(l.balances()["producer"],70)
        self.assertEqual(sum(l.balances().values()),0)
        with self.assertRaises(ValueError):l.post("p","S04","producer","A-B",1,101,"day","r","m1")
        with self.assertRaises(ValueError):statement(l,"monthly","2026-11",[1,2],[1])

    def test_penalty_exclusion_and_persistent_counter(self):
        config=dict(threshold=1,rate=10,minimum=1,cap=1000,escalation=.1,source_clause="synthetic")
        event=dict(family="systematic_RES_deviation",quantity=3,external_RES_control=True)
        r=assess_penalty(event,config,2)
        self.assertEqual(D(r["amount_eur"]),0);self.assertEqual(r["counter_after"],2)
        event["external_RES_control"]=False
        r=assess_penalty(event,config,2)
        self.assertEqual(D(r["amount_eur"]),24);self.assertEqual(r["counter_after"],3)

    def test_offsetting_offers_not_double_settled(self):
        s=synthetic_day();period=s["periods"][0]
        regs={e:Registration(e,e,"brp","member","meter","production","HV",tolerance=0)
              for e in ("up","down")}
        rtus=[]
        for i in range(6):
            common=dict(price_forming=True,reason="ordinary_market",agc=False,
                        up_offer_eur_mwh=120,down_offer_eur_mwh=50)
            rtus.append(dict(period=0,price_eur_mwh=999,rows=[
                dict(entity="up",up_mwh=1,down_mwh=0,actual_mwh=2,**common),
                dict(entity="down",up_mwh=0,down_mwh=1,actual_mwh=1,**common)]))
        ledger=Ledger()
        bridges,prices=settle_energy(ledger,[period],rtus,regs,{"up":[6],"down":[12]},
                                      {0:[[80]*6,[100]*6,[120]*6]})
        self.assertTrue(prices[0]["historical_fallback"])
        cash={e.account:D(e.cash_eur) for e in ledger.entries if e.component=="S03"}
        self.assertEqual(cash["participant:up:nonDAM"],720)
        self.assertEqual(cash["participant:down:nonDAM"],-300)
        self.assertTrue(all(b["ordinary_instq_mwh"]==0 for b in bridges))

    def test_energy_award_with_ramp_and_hold(self):
        s=synthetic_day();u=s["units"][2]
        state={u.id:dict(on=0,end_mw=0,duration_rtus=120)}
        row=dict(on=1,agc=0,reserve_mw={p:0 for p in PRODUCTS})
        result=clear_rtu(s["rtus"][0],[u],state,{u.id:row},{u.id:0},12,0,12,0)
        r=result["rows"][0]
        self.assertAlmostEqual(r["up_mwh"],1)
        self.assertAlmostEqual(r["actual_mwh"],1)
        self.assertLess(r["actual_terminal_mw"],24)
        self.assertEqual(result["capacity_products"],[])


class FinancialTests(unittest.TestCase):
    def test_margin_and_default(self):
        self.assertEqual(D(required_margin(1000,200,[100,200,300,100,200,200],800)["total"]),2400)
        with self.assertRaises(ValueError):required_margin(1000,200,[],800)
        r=default_waterfall(1000,600,200,{"other":150},500)
        self.assertEqual(D(r["draws_eur"]["reserve_fund"]),50)
        self.assertEqual(D(r["reserve_remaining_eur"]),450)
        self.assertEqual(D(r["uncovered_loss_eur"]),0)
        self.assertEqual(D(default_waterfall(2000,600,200,{"other":150},500)["uncovered_loss_eur"]),550)
        self.assertEqual(D(default_waterfall(100,600,200,{"other":150},500)["replenishment_required_eur"]),0)
        self.assertEqual(D(late_payment_charge(1000,29)["charge_eur"]),0)
        self.assertEqual(D(late_payment_charge(1000,30)["charge_eur"]),500)

    def test_invoice_rounding_payment_holiday(self):
        i=invoice("1",["100.005"],date(2026,11,6),".19",{date(2026,11,9)})
        self.assertEqual(i["net_eur"],"100.01")
        self.assertEqual(i["tax_eur"],"19.00")
        self.assertEqual(i["due_on"],"2026-11-10")
        i=pay(i,50);self.assertEqual(i["state"],"partially_paid")
        i=pay(i,D(i["gross_eur"])-50);self.assertEqual(i["state"],"paid")
        with self.assertRaises(ValueError):pay(i,1)

    def test_clearing_freeze_member_netting(self):
        positions=[dict(id="1",member="m1",clearing_account="a",category="DAM",value_eur="100",reference_period="day",source="trade"),
                   dict(id="2",member="m1",clearing_account="a",category="DAM",value_eur="-30",reference_period="day",source="trade2"),
                   dict(id="3",member="m2",clearing_account="b",category="DAM",value_eur="-70",reference_period="day",source="trade3")]
        c=ClearingCycle("cycle",{"m1","m2"})
        result=c.finalise(positions)
        self.assertEqual(result["net_positions"][0]["receipt_eur"],"70")
        self.assertEqual(c.finalise(positions)["hash"],result["hash"])
        positions[0]["value_eur"]="101";positions[2]["value_eur"]="-71"
        with self.assertRaises(ValueError):c.finalise(positions)


class MeteringTests(unittest.TestCase):
    def test_loss_and_profile_conservation(self):
        m=Meter("m","l","p","100","1.05","offtake","certified",1,datetime.now(timezone.utc))
        self.assertEqual(m.adjusted(),D(105))
        profile=allocate_profile(100,{"a":1,"b":2,"c":3})
        self.assertEqual(sum(profile.values()),100)
        with self.assertRaises(ValueError):allocate_profile(100,{"a":0})

    def test_storage_power_soc_and_eligibility(self):
        config=dict(initial_soc_mwh=10,capacity_mwh=20,power_mw=12,efficiency_charge=.9,efficiency_discharge=.9)
        rows=storage_delivery(config,[5,0],[0,4.05],[.5,.5],[1,1])
        self.assertEqual(D(rows[-1]["final_soc_mwh"]),10)
        with self.assertRaises(ValueError):storage_delivery(config,[10],[0],[.5],[1])
        with self.assertRaises(ValueError):storage_delivery(config,[0],[20],[2])
        with self.assertRaises(ValueError):validate_rtbm_eligibility("storage",True)
        validate_rtbm_eligibility("thermal")

    def test_storage_exemption_is_meter_role_bounded(self):
        now=datetime.now(timezone.utc)
        meters=[Meter("c","s","p","10","1","charging","certified",1,now),
                Meter("a","s","p","2","1","auxiliary","certified",1,now),
                Meter("l","s","p","100","1","offtake","certified",1,now)]
        result=eligible_fee_base(meters,"TUOS",8)
        self.assertEqual(D(result["chargeable_mwh"]),102)
        self.assertEqual(D(result["exempt_mwh"]),10)


if __name__=="__main__":unittest.main()

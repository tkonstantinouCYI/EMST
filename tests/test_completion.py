import unittest
from unittest.mock import patch
from dataclasses import replace
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from emst.settlement import D,Ledger,settle_energy
from emst.compliance import assess,Counters,systematic,mandatory_penalty,CAPACITY,daily_activation_penalties
from emst.accounting import (tariff_lines,ancillary_recovery,allocate,Recalculation,reconcile_profiles,
                                 monthly_calendar,annex_energy_cash,reconciliation_calendar)
from emst.operations import priority_curtailment,trip,rerun_horizon,emergency_energy,contingency_energy,tied_selection,project_handover
from emst.risk import minimum_margin,Collateral,DefaultState
from emst.scenario import synthetic_day
from emst.isp import schedule
from emst.rtbm import clear_rtu
from emst.model import PRODUCTS,Order,Registration
from emst.commercial import clear_dam,forward_schedules
from emst.notifications import Notification,NotificationBook
from emst.lifecycle import StatementBook,CashBook,invoice_correction
from emst.clearing import invoice
from emst.procurement import FrozenShares,storage_auction,storage_service_cash
from emst.history import settle_history,synthetic_tariffs


class ComplianceTests(unittest.TestCase):
    def test_day_observable_activation_penalty_needs_no_monthly_history(self):
        s=synthetic_day();ledger=Ledger()
        rows=[dict(entity="thermal_A",period=2,MSQ=40,MQ=40,accepted_net_mwh=2)]
        result=daily_activation_penalties(ledger,rows,s["registrations"],s["day"],
            dict(rate=100,escalation=.1,tolerance=.02,provenance="synthetic"),{"thermal_A":2})
        # Instruction 42, lower tolerated delivery 41.16; excess 1.16 MWh.
        self.assertEqual(D(result[0]["amount_eur"]),D("127.6"))
        self.assertEqual(ledger.balances()["participant:generator_A:nonDAM"],D("-127.6"))
        self.assertEqual(ledger.balances()["A-E"],D("127.6"))

    def base(self,family):
        e=dict(entity="g",family=family,obligated=True,failure=True,counter_window="2026",
               occurrence_key="2026-11-05",event_time="2026-11-05T00:00:00+02:00",
               assessment_period="2026-11",missing_quantities=[40,60])
        p=dict(rate=2,escalation=.1,exponent=.5,provenance="synthetic")
        return e,p,Counters({("g",family,"2026"):3})

    def test_each_capacity_family_exponent_and_persistent_day(self):
        for family in CAPACITY:
            e,p,c=self.base(family)
            self.assertEqual(D(assess(e,p,c)["amount_eur"]),440)
            self.assertEqual(D(assess(e,p,c)["amount_eur"]),440)
            self.assertEqual(assess(e,p,c)["counter_after"],4)

    def test_non_capacity_penalty_formulas(self):
        fixtures=[("ancillary_non_delivery",dict(missing_mFRR_fraction=.2,mFRR_remuneration_eur=100,missing_RR_fraction=.5,RR_remuneration_eur=40),88),
            ("invalid_physical_notification",dict(mismatch_mwh=2,dam_price_eur_mwh=100),60),
            ("minimum_DAM_percentage",dict(dominant_supplier=True,net_delivery_mwh=80,dam_buy_mwh=20),30000),
            ("dispatch_failure",dict(absolute_power_deviations_mw=[1,3]),17.6),
            ("significant_activation_failure",dict(schedule_mwh=40,up_mwh=2,down_mwh=0,meter_mwh=40),2.552),
            ("missing_black_start_offer",{},D(500000)/12)]
        for family,inputs,expected in fixtures:
            e,p,c=self.base(family);e.update(inputs)
            p.update(mismatch_coefficient=.3,minimum_fraction=.5,dam_cap=1000,tolerance=.02)
            self.assertAlmostEqual(float(D(assess(e,p,c)["amount_eur"])),float(expected),places=8)

    def test_R08_RES_exclusion(self):
        e,p,c=self.base("significant_activation_failure");e["role"]="RES"
        r=assess(e,p,c)
        self.assertEqual(D(r["amount_eur"]),0);self.assertEqual(r["counter_after"],3)
        self.assertIn("R08",r["exclusion_reason"])

    def test_monthly_symmetric_denominators_and_PA(self):
        records=[dict(period="a",schedule_mwh=10,meter_mwh=12,adjusted_schedule_mwh=10),
                 dict(period="b",schedule_mwh=10,meter_mwh=8,adjusted_schedule_mwh=10)]
        rates=dict(absolute_rate=100,rms_rate=50,absolute_tolerance=.1,rms_tolerance=.1)
        r=systematic(records,["a","b"],"load",rates)
        self.assertEqual(D(r["amount_eur"]),40)
        self.assertEqual(D(r["absolute_denominator"]),20)
        records=[dict(period="a",schedule_mwh=10,meter_mwh=8,down_mwh=2),
                 dict(period="b",schedule_mwh=10,meter_mwh=0,down_mwh=10,external_control=True)]
        self.assertEqual(D(systematic(records,["a","b"],"RES",rates)["amount_eur"]),0)
        with self.assertRaises(ValueError):systematic(records[:1],["a","b"],"RES",rates)

    def test_contracted_and_mandatory_penalties(self):
        e,p,_=self.base("contracted_unit_failure")
        e.update(failed_settlement_periods=3,registered_capacity_mw=10,contingency_price=5)
        c=Counters({("g",e["family"],"2026"):0})
        self.assertEqual(D(assess(e,p,c)["amount_eur"]),300)
        e["occurrence_key"]="2026-11-06"
        self.assertEqual(D(assess(e,p,c)["amount_eur"]),438000)
        self.assertEqual(mandatory_penalty("producer",2,10,5,.1,2),121)


class LaterAccountingTests(unittest.TestCase):
    def test_daily_run_settles_only_solved_day_without_complete_period_fixtures(self):
        from pathlib import Path
        import contextlib,io
        from emst.runner import run_day
        project=Path(__file__).resolve().parents[1]
        scratch=(project/"tmp").resolve()
        self.assertTrue(scratch.is_relative_to(project))
        scratch.mkdir(exist_ok=True)
        directory=scratch/"daily_scope_regression";directory.mkdir(exist_ok=True)
        exports={}
        with contextlib.redirect_stdout(io.StringIO()), \
             patch("emst.runner.write_json",side_effect=lambda path,value:exports.__setitem__(path.stem,value)), \
             patch("emst.runner.full_cycles",side_effect=AssertionError("Monthly fixture must not execute")), \
             patch("emst.runner.later_stage_audit",side_effect=AssertionError("Later-cycle audit must not execute")):
            manifest,audit=run_day(output=directory)
            self.assertTrue(audit["passed"],audit["failures"][:5])
            self.assertTrue(manifest["selected_scope_calculation_complete"])
            self.assertTrue(manifest["daily_calculation_complete"])
            self.assertFalse(manifest["calculation_complete"])
            self.assertFalse(manifest["accounting_cycles_enabled"])
            read=lambda name:exports[name]
            self.assertEqual(read("accounting_cycles")["status"],"skipped_by_scope")
            self.assertEqual(read("coverage")["S29"]["status"],"deferred_by_scope")
            self.assertTrue(read("daily_penalties"))
            self.assertTrue(any(e["component"]=="S04" for e in read("ledger")))
            self.assertTrue(all(e["period"].startswith(manifest["day"]) for e in read("ledger")))
            self.assertEqual(read("dam")["blocks"]["storage_cycle"],1)
            self.assertEqual(D(read("storage")[-1]["final_soc_mwh"]),20)

    def test_rejected_circular_storage_block_does_not_run_proposed_cycle(self):
        from pathlib import Path
        import contextlib,io
        from emst.runner import run_day
        s=synthetic_day()
        s["orders"]=[replace(o,price_eur_mwh=0 if o.side=="buy" else 1000) if o.block=="storage_cycle" else o for o in s["orders"]]
        directory=Path(__file__).resolve().parents[1]/"tmp"/"rejected_storage_regression"
        directory.mkdir(parents=True,exist_ok=True);exports={}
        with contextlib.redirect_stdout(io.StringIO()),patch("emst.runner.write_json",side_effect=lambda path,value:exports.__setitem__(path.stem,value)):
            manifest,audit=run_day(output=directory,scenario=s)
        self.assertTrue(audit["passed"],audit["failures"][:5])
        self.assertEqual(exports["dam"]["blocks"]["storage_cycle"],0)
        self.assertEqual(D(manifest["metrics"]["storage_charge_mwh"]),0)
        self.assertEqual(D(manifest["metrics"]["storage_discharge_mwh"]),0)
        self.assertTrue(all(D(r["initial_soc_mwh"])==D(r["final_soc_mwh"])==20 for r in exports["storage"]))

    def test_forced_outage_is_uninstructed_in_monthly_replay(self):
        regs={"g":Registration("g","BSP","BRP","member","meter","production","HV",tolerance=0),
              "l":Registration("l","load","load","member","meterL","offtake","HV",tolerance=0)}
        common=dict(date="2026-11-05",period="2026-11-05:18",period_index=18,hours=.5,
            price_eur_mwh=100,payment_inputs=dict(system="short",last_up=120,last_down=50,offset_legs=[]),
            accepted_net_mwh=0,agc=True,tolerance=0,external_control=False,down_mwh=0,service_awards=[],
            purpose="consumption",power_deviation_samples_mw=[0]*6)
        rows=[dict(common,id="g",entity="g",brp_participant="BRP_owner",schedule_mwh=10,meter_mwh=0,
                   forced_unavailable=True,forced_instq_mwh=0),
              dict(common,id="l",entity="l",brp_participant="load",schedule_mwh=10,meter_mwh=10)]
        rates=dict(absolute_rate=100,rms_rate=100,absolute_tolerance=.02,rms_tolerance=.02)
        ledger,_=settle_history(rows,regs,synthetic_tariffs(),{"load":1},0,0,
            {"UA-1":D(".4"),"UA-6":D(".3"),"UA-7":D(".3")},penalty_rates=rates,
            activation_parameters={},opening_activation_counters={})
        self.assertEqual(D(next(e.cash_eur for e in ledger.entries if e.id=="inst:g")),0)
        uninst=next(e for e in ledger.entries if e.id=="uninst:g")
        self.assertEqual(D(uninst.cash_eur),-1000)
        self.assertEqual(uninst.account,"participant:BRP_owner:nonDAM")

    def test_BSP_instruction_and_external_BRP_imbalance_stay_separate(self):
        period=synthetic_day()["periods"][0]
        reg=Registration("g","BSP","externalBRP","member","meter","production","HV",tolerance=0)
        rtus=[dict(period=0,price_eur_mwh=100,rows=[dict(entity="g",up_mwh=1,down_mwh=0,
            actual_mwh=2,price_forming=True,reason="ordinary_market",agc=False,
            up_offer_eur_mwh=120,down_offer_eur_mwh=50)]) for _ in range(6)]
        ledger=Ledger()
        bridges,_=settle_energy(ledger,[period],rtus,{"g":reg},{"g":[0]},
            {0:[[100]*6]*3},{"externalBRP":"portfolio_owner"})
        instruction=[e for e in ledger.entries if e.component=="S03"]
        imbalance=[e for e in ledger.entries if e.component=="S04"]
        self.assertEqual(instruction[0].account,"participant:BSP:nonDAM")
        self.assertEqual(imbalance[0].account,"participant:portfolio_owner:nonDAM")
        self.assertEqual(D(imbalance[0].cash_eur),600)
        self.assertEqual(bridges[0]["UNINSTQ"],6)

    def test_annex_signed_revision_uses_historical_IMSP(self):
        old=[dict(id="load",participant="s",period="p",role="offtake",meter_mwh=10,price_eur_mwh=80),
             dict(id="gen",participant="g",period="p",role="production",meter_mwh=20,price_eur_mwh=80)]
        new=[{**old[0],"meter_mwh":12,"price_eur_mwh":999},{**old[1],"meter_mwh":21}]
        amounts={r["id"]:D(r["cash_eur"]) for r in annex_energy_cash(old,new)}
        self.assertEqual(amounts,{"load":D(-160),"gen":D(80)})
        cal=reconciliation_calendar(2026,11,frozenset())
        self.assertEqual(cal["quantity_submission"],"2027-02-15")
        self.assertEqual(cal["preliminary"],"2027-02-22")
        self.assertEqual(cal["final"],"2027-03-08")

    def test_representation_revision_reverses_old_account(self):
        old,new,target=Ledger(),Ledger(),Ledger()
        old.post("same","S14","participant:old:nonDAM","A-F",-10,1,"p","13.6","meter")
        new.post("same","S14","participant:new:nonDAM","A-F",-10,1,"p","13.6","meter")
        engine=Recalculation()
        self.assertEqual(engine.apply(target,old,new,2,"meter_reconciliation"),0)
        self.assertEqual(target.balances()["participant:old:nonDAM"],10)
        self.assertEqual(target.balances()["participant:new:nonDAM"],-10)
        self.assertEqual(engine.apply(target,old,new,2,"meter_reconciliation"),0)

    def tariffs(self):
        return {v:dict(tuos_energy=10,tuos_power=2,duos_energy=3 if v=="MV" else 4,
                       duos_power=1,RES=3,PSO=2,admin=1) for v in ("HV","MV","LV")}

    def test_voltage_cascade_power_units_storage_aux(self):
        m=dict(energy_mwh=5,hours=.5,voltage="LV",purpose="consumption")
        r=tariff_lines(m,self.tariffs())
        self.assertEqual(r["S14"],70);self.assertEqual(r["S15"],55)
        m["purpose"]="eligible_storage_charging"
        self.assertTrue(all(v==0 for v in tariff_lines(m,self.tariffs()).values()))
        m["purpose"]="auxiliary";self.assertEqual(tariff_lines(m,self.tariffs())["S14"],70)

    def test_ancillary_recovery_uses_two_different_bases(self):
        r,convention=ancillary_recovery(100,60,{"loadA":1,"loadB":3},{"g":2,"l":1},
                                      {"g":"generator","l":"loadB"},"fail")
        self.assertEqual(r,{"loadA":D(25),"loadB":D(95),"generator":D(40)})
        self.assertIsNone(convention)
        with self.assertRaises(ValueError):ancillary_recovery(0,60,{"load":1},{"g":0},{"g":"g"},"fail")

    def test_recompute_all_components_and_idempotence(self):
        old,new,target=Ledger(),Ledger(),Ledger()
        for l,q in ((old,10),(new,12)):
            l.post("energy","S04","participant:g:nonDAM","A-B",q,100,"m","13.4","meter")
            l.post("tariff","S14","participant:l:nonDAM","A-F",-q,12,"m","13.6","meter")
            l.post("mandatory","S24","participant:g:nonDAM","MM",10,5,"m","MM","award")
        revision=Recalculation()
        self.assertEqual(revision.apply(target,old,new,2,"meter_reconciliation",{"S24"}),176)
        self.assertEqual(revision.apply(target,old,new,2,"meter_reconciliation",{"S24"}),0)
        self.assertEqual(target.balances()["participant:g:nonDAM"],200)
        self.assertEqual(target.balances()["participant:l:nonDAM"],-24)

    def test_profiles_and_monthly_calendar(self):
        r=reconcile_profiles(100,{"n":40},{"a":1,"b":2},1.05)
        self.assertEqual(sum(r.values()),105);self.assertEqual(r["n"],42)
        cal=monthly_calendar(2026,11,frozenset())
        self.assertEqual(cal["calculation_start"],"2026-12-14")
        self.assertEqual(cal["preliminary"],"2026-12-16")


class OperationalTests(unittest.TestCase):
    def test_forward_buyer_is_participant_and_entity_allocation_is_owned(self):
        s=synthetic_day();units={u.id:u for u in s["units"]};c=s["forwards"][0]
        self.assertEqual(c.buyer,"supplier_T")
        self.assertEqual(c.buyer_entity,"load_T")
        schedules,_=forward_schedules([c],s["registrations"],units,48)
        self.assertEqual(schedules["load_T"],[15]*48)
        for bad in (replace(c,buyer="load_T"),replace(c,buyer="supplier_D"),replace(c,buyer_entity="load_D")):
            with self.assertRaises(ValueError):forward_schedules([bad],s["registrations"],units,48)
        inferred,_=forward_schedules([replace(c,buyer_entity=None)],s["registrations"],units,48)
        self.assertEqual(inferred,schedules)
        gate=s["initial_cutoff"];book=NotificationBook([c],gate,gate)
        with self.assertRaises(ValueError):book.submit(Notification(c.id,"offtake","load_T",c.energy_mwh,1,gate))
        book.submit(Notification(c.id,"offtake","supplier_T",c.energy_mwh,1,gate))

    def test_LP_shadow_prices_default_and_optional_ranges_do_not_reprice(self):
        s=synthetic_day();u=s["units"][1]
        args=([u],s["periods"][:2],[50,50],[0,0],{u.id:[25,25]},
            {p:[2,2] if p=="FCR_up" else [0,0] for p in PRODUCTS},
            {u.id:dict(on=1,end_mw=50,duration_periods=10)},s["initial_cutoff"],"shadow_price_test")
        # Default path must perform no face construction or RHS re-solves.
        with patch("emst.isp.dual_face",side_effect=AssertionError("Unexpected range solve")), \
             patch("emst.isp.perturbation",side_effect=AssertionError("Unexpected perturbation")):
            normal=schedule(*args)
        self.assertEqual(normal["reserve_dual_ranges"],{})
        self.assertEqual(normal["perturbations"],[])
        self.assertEqual(normal["pricing_method"],"fixed_discrete_lp_shadow_prices_v2")
        # Optional diagnostics may calculate ranges, but never select a different
        # settlement vector or impose lexicographic restrictions on that face.
        with patch("emst.optimization.NativeFace.fix",side_effect=AssertionError("Unexpected price selection")):
            diagnosed=schedule(*args,price_ranges=True)
        for a,b in zip(normal["rows"],diagnosed["rows"]):
            self.assertAlmostEqual(a["reserve_price_eur_mw_period"]["FCR_up"],2.5)
            self.assertEqual(a["reserve_price_eur_mw_period"],b["reserve_price_eur_mw_period"])
        self.assertEqual(normal["frozen_discrete"],diagnosed["frozen_discrete"])
        self.assertEqual(normal["lp_reserve_shadow_prices"],diagnosed["lp_reserve_shadow_prices"])
        self.assertEqual(len(diagnosed["reserve_dual_ranges"]),2)

    def test_noon_projection_keeps_actual_state_and_uses_only_forecasts(self):
        from copy import deepcopy
        s=synthetic_day();u=s["units"][0];cutoff=s["rerun_cutoff"]
        end=cutoff+timedelta(hours=1)
        intervals=[t for t in s["rtus"] if cutoff<=t.start<end]
        state={u.id:dict(on=1,end_mw=160,duration_rtus=100)};before=deepcopy(state)
        awards={(u.id,p):dict(on=1,agc=1,reserve_mw={k:int(k in ("aFRR_up","aFRR_down")) for k in PRODUCTS}) for p in (22,23)}
        projection=project_handover([u],intervals,state,awards,{u.id:[80]*48},[160]*48,[0]*48,[0]*48,
                                   s["history"],cutoff,end,3026)
        self.assertEqual(state,before)
        self.assertEqual(len(projection["trace"]),12)
        self.assertEqual(projection["projected_state"][u.id]["duration_rtus"],112)
        self.assertEqual(projection["trace"][-1]["end"],end.astimezone(intervals[0].start.tzinfo).isoformat())
        # Forecast revision changes the planned noon state; realised noon data
        # and failure events are deliberately not arguments to this calculation.
        changed=project_handover([u],intervals,state,awards,{u.id:[80]*48},[150]*48,[0]*48,[0]*48,
                                s["history"],cutoff,end,3026)
        self.assertGreater(abs(projection["projected_state"][u.id]["end_mw"]-changed["projected_state"][u.id]["end_mw"]),5)

    def test_priority_tools_and_instructed_tags(self):
        pools=[dict(id=id,category=cat,available_mwh=q,technical_minimum_mwh=0,zero_injection_self_consumption=False)
               for id,cat,q in (("protected",1,10),("priority",2,20),("ordinary",3,30))]
        r=priority_curtailment(pools,40,123)
        self.assertEqual({x["entity"]:D(x["down_mwh"]) for x in r},{"ordinary":30,"priority":10})
        self.assertTrue(all(x["external_control"] and not x["price_forming"] for x in r))
        with self.assertRaises(RuntimeError):priority_curtailment(pools,51,123)
        self.assertEqual(tied_selection(["a","b","c"],3),tied_selection(["c","b","a"],3))

    def test_trip_and_future_only_horizon(self):
        s=synthetic_day();at=s["periods"][10].start+timedelta(minutes=5)
        state={"g":dict(on=1,end_mw=50,duration_rtus=30)}
        record=trip(state,"g",at)
        self.assertEqual(record["prior_state"]["end_mw"],50)
        self.assertEqual(state["g"]["end_mw"],0)
        self.assertEqual(rerun_horizon(s["periods"],at)[0].period,11)

    def test_real_emergency_dispatch_and_separate_payment(self):
        s=synthetic_day();u=replace(s["units"][0],maximum_mw=10,minimum_mw=0,initial_mw=10,agc_min_mw=0)
        state={u.id:dict(on=1,end_mw=10,duration_rtus=30)}
        r=clear_rtu(s["rtus"][0],[u],state,{u.id:dict(on=1,agc=1,reserve_mw={p:0 for p in PRODUCTS})},
            {u.id:10/12},12,0,12,0,emergency_resources=[dict(id="contingency",available_mw=5,contract_rate=300)],emergency_price=1000)
        self.assertEqual(r["regime"],"emergency")
        self.assertAlmostEqual(r["emergency_mwh"],2/12)
        self.assertEqual(r["price_eur_mwh"],1000)
        self.assertEqual(D(contingency_energy(2,1,300)["cash_eur"]),300)
        self.assertEqual(D(emergency_energy(10,7,8,.5,100)["cash_eur"]),200)

    def test_cold_start_soaking_and_AGC_exclusion(self):
        s=synthetic_day();u=replace(s["units"][2],maximum_mw=60,minimum_mw=20,
            startup_trajectory_mw=(5,10,20),soak_periods=3,initial_duration=100,agc_min_mw=0)
        r=schedule([u],s["periods"][:4],[2.5,7.5,15,20],[0]*4,{u.id:[0]*4},
            {p:[0]*4 for p in PRODUCTS},{u.id:dict(on=0,end_mw=0,duration_periods=100)},
            s["initial_cutoff"],"cold_soak",False)
        self.assertEqual([x["power_mw"] for x in r["rows"][:3]],[2.5,7.5,15])
        self.assertTrue(all("end_mw" not in x for x in r["rows"]))
        self.assertEqual(r["rows"][0]["startup_class"],"cold")
        self.assertEqual(r["rows"][0]["startup_cost_eur"],270)
        self.assertEqual([x["agc"] for x in r["rows"]],[0,0,0,1])

    def test_circular_storage_order_and_notification_correction(self):
        orders=[Order("charge","s","s",0,"buy",2,150,"cycle",1,circular=True),
                Order("discharge","s","s",1,"sell",1.8,0,"cycle",1,circular=True),
                Order("gen0","g","g",0,"sell",10,50),Order("gen1","g","g",1,"sell",10,100),
                Order("d0","l","l",0,"buy",3,200),Order("d1","l","l",1,"buy",3,200)]
        r=clear_dam(orders,2)
        self.assertEqual(r["blocks"]["cycle"],1)
        self.assertEqual(r["accepted_mwh"]["charge"],2)
        s=synthetic_day();c=s["forwards"][0];gate=s["initial_cutoff"]
        book=NotificationBook([c],gate,gate+timedelta(hours=1))
        book.submit(Notification(c.id,"delivery",c.seller,c.energy_mwh,1,gate))
        book.submit(Notification(c.id,"offtake",c.buyer,tuple(q+1 for q in c.energy_mwh),1,gate))
        self.assertTrue(book.finalise()[1])
        book.submit(Notification(c.id,"offtake",c.buyer,c.energy_mwh,2,gate+timedelta(minutes=1)))
        self.assertEqual(book.finalise()[0],[c])

    def test_monthly_RR_is_fixed_input_and_not_an_ISP_dual(self):
        s=synthetic_day();u=s["units"][1]
        r=schedule([u],s["periods"][:2],[50,50],[0,0],{u.id:[25,25]},
            {p:[2,2] if p=="RR_up" else [0,0] for p in PRODUCTS},
            {u.id:dict(on=1,end_mw=50,duration_periods=10)},s["initial_cutoff"],"RR_contract",True,
            rr_awards={u.id:[2,2]},rr_prices=[7,7])
        self.assertEqual(r["rows"][0]["reserve_price_status"]["RR_up"],"monthly_contract")
        self.assertEqual(r["rows"][0]["reserve_price_eur_mw_period"]["RR_up"],7)
        self.assertNotIn("requirement_RR_up_0",r["reserve_dual_ranges"])

    def test_shortfall_price_does_not_claim_physical_security(self):
        s=synthetic_day();u=replace(s["units"][1],reserve_limit_mw=1)
        r=schedule([u],s["periods"][:1],[50],[0],{u.id:[25]},
            {p:[10] if p=="FCR_up" else [0] for p in PRODUCTS},
            {u.id:dict(on=1,end_mw=50,duration_periods=10)},s["initial_cutoff"],"reserve_shortfall",True,
            reserve_shortage_cap=100)
        self.assertTrue(r["shortage"])
        self.assertAlmostEqual(r["reserve_shortfalls_mw"]["FCR_up"][0],9)
        self.assertAlmostEqual(r["rows"][0]["reserve_price_eur_mw_period"]["FCR_up"],100,places=5)

    def test_actual_above_registered_emergency_dispatch(self):
        s=synthetic_day();u=replace(s["units"][0],maximum_mw=10,minimum_mw=0,initial_mw=10,agc_min_mw=0)
        r=clear_rtu(s["rtus"][0],[u],{u.id:dict(on=1,end_mw=10,duration_rtus=60)},
            {u.id:dict(on=1,agc=0,reserve_mw={p:0 for p in PRODUCTS})},{u.id:10/12},12,0,12,0,
            emergency_resources=[dict(id="extra",host_entity=u.id,kind="above_registered",available_mw=6,
                                      contract_rate=100,extra_ramp_mw_hour=120)],emergency_price=1000)
        e=r["emergency_rows"][0]
        self.assertAlmostEqual(e["extra_terminal_mw"],4,delta=1e-6)
        self.assertLessEqual(e["extra_terminal_mw"],6)

    def test_MM_frozen_inherent_allocation_and_storage_auction(self):
        shares=FrozenShares.from_absorption("2026-09","MY_frozen",{"T":6000,"D":4000})
        self.assertEqual(shares.inherent_energy([20],.5),{"T":D(6),"D":D(4)})
        original=shares.fingerprint
        shares.inherent_energy([30],.5)
        self.assertEqual(shares.fingerprint,original)
        award=storage_auction({"a":2,"b":1},2,10)
        self.assertEqual(sum(D(v) for v in award["awards_mw"].values()),2)
        self.assertEqual(storage_service_cash(2,1,2,4,1.2),D("9.6"))


class RiskTests(unittest.TestCase):
    def test_role_minimum_and_expired_collateral(self):
        r=minimum_margin([dict(role="generator",capacity_factors_m7_to_m12=[.5]*6,
             capacity_mw_or_authorised_mva=10,imbalance_factor=.1)],100,[10],3)
        self.assertEqual(D(r["total_eur"]),73200)
        expired=Collateral("x","a","guarantee","1000","0",date(2026,11,4))
        self.assertEqual(expired.eligible(date(2026,11,5)),0)

    def test_sequential_defaults_cannot_reuse_drawn_security(self):
        c=Collateral("cash","a","cash","600","0",None)
        state=DefaultState({"cash":c},{"m":200,"other":150},500)
        a=state.default("1","m","a",1000,date(2026,11,5))
        b=state.default("2","m","a",500,date(2026,11,6))
        self.assertEqual(D(a["uncovered_loss_eur"]),0)
        self.assertEqual(D(b["uncovered_loss_eur"]),50)
        self.assertEqual(c.eligible(date(2026,11,6)),0)
        self.assertEqual(state.liabilities["m"],1500)

    def test_statement_objection_versions_and_invoice_delta(self):
        at=datetime(2026,11,5,15,tzinfo=timezone.utc)
        book=StatementBook();first=book.preliminary("s",{"cash":100},at,at+timedelta(hours=1))
        first["lines"]["cash"]=999
        self.assertEqual(book.versions["s"][0]["lines"]["cash"],100)
        book.object("s","p","meter correction",at+timedelta(minutes=30))
        final=book.finalise("s",{"cash":70},at+timedelta(hours=2),["accepted"])
        self.assertEqual(final["version"],2)
        with self.assertRaises(ValueError):book.finalise("s",{},at,[])
        original=invoice("i",[100],date(2026,11,5),0)
        adjustment=invoice_correction(original,70,date(2026,11,6),0,frozenset())
        self.assertEqual(D(adjustment["net_eur"]),-30)
        self.assertEqual(adjustment["previous"],"i")

    def test_cash_conservation_and_collateral_restriction(self):
        b=CashBook({"member":1000,"clearing":0},restricted={"member":800})
        at=datetime(2026,11,5,11,tzinfo=timezone.utc)
        b.transfer("1","member","clearing",200,at,"invoice")
        self.assertEqual(sum(b.balances.values()),1000)
        self.assertEqual(b.transfer("1","member","clearing",200,at,"invoice"),0)
        with self.assertRaises(ValueError):b.transfer("2","member","clearing",1,at,"invoice2")


if __name__=="__main__":unittest.main()

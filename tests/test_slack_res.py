import unittest
from datetime import date,datetime,timezone
from emst import Market,Ruleset,ThermalState
from emst.calendar import day_intervals
from emst.model import PRODUCTS,Unit
from emst.dispatch import dispatch
from emst.isp import schedule
from emst.optimization import highspy


class SlackOfferTests(unittest.TestCase):
    def test_energy_and_directional_reserve_shortage_prices_and_costs(self):
        config={'energy_shortage_eur_mwh':1000.,'reserve_shortage_eur_mw_mtu':{'FCR_up':100.}}
        m=Market({'slack':config});m.add_participant('p')
        a=m.add_thermal('u','p',capacity_mw=50,minimum_mw=0,marginal_cost=50,
            initial_state=ThermalState('dispatch',50,180),ramp_mw_hour=600)
        periods=day_intervals(date(2026,11,5),30)[:1]
        req={p:[10 if p=='FCR_up' else 0] for p in PRODUCTS}
        r=dispatch([a],{'u':dict(stage='dispatch',on=1,power_mw=50,duration_minutes=180)},
            periods,[80],[0],{'u':[0]},req,m.ruleset.to_dict())
        unit=Unit('u','p',50,0,50,600,50,1,6)
        legacy=schedule([unit],periods,[80],[0],{'u':[0]},req,
            {'u':dict(on=1,end_mw=50,duration_periods=6)},datetime(2026,11,4,tzinfo=timezone.utc),
            'shortage_test',slack_parameters=config)
        for result in (r,legacy):
            s=result['slack_by_period'][0]
            self.assertAlmostEqual(s['energy_shortage_mwh'],15)
            self.assertAlmostEqual(s['reserve_shortage_mw']['FCR_up'],10)
            self.assertAlmostEqual(result['fixed_lp_objective_eur'],17250)
            self.assertFalse(result['physical_security_feasible'])
            self.assertAlmostEqual(result['objective_components_eur']['energy_shortage_penalty'],15000)
            self.assertAlmostEqual(result['objective_components_eur']['reserve_shortage_penalty'],1000)
        self.assertAlmostEqual(r['rows'][0]['price_eur_mwh'],1000)
        self.assertAlmostEqual(r['rows'][0]['reserve_prices']['FCR_up'],100)

    def test_energy_surplus_and_disabled_slack(self):
        m=Market({'slack':{'energy_surplus_eur_mwh':1000}});m.add_participant('p')
        a=m.add_thermal('u','p',capacity_mw=50,minimum_mw=20,marginal_cost=50,
            minimum_up_minutes=180,initial_state=ThermalState('dispatch',20,0))
        args=([a],{'u':dict(stage='dispatch',on=1,power_mw=20,duration_minutes=0)},
              day_intervals(date(2026,11,5),30)[:1],[0],[0],{'u':[0]},{p:[0] for p in PRODUCTS})
        r=dispatch(*args,m.ruleset.to_dict())
        self.assertAlmostEqual(r['slack_by_period'][0]['energy_surplus_mwh'],10)
        self.assertAlmostEqual(r['rows'][0]['price_eur_mwh'],-1000)
        rules=m.ruleset.to_dict();rules['slack']['enabled']=False
        with self.assertRaises(RuntimeError):dispatch(*args,rules)

    def test_RES_down_merit_order_and_no_capacity(self):
        m=Market()
        for owner,price in (('a',-20),('b',-5)):
            m.add_participant(owner)
            m.add_res(owner,owner,generation_forecast=[50]*48,actual_generation=[50]*48,energy_down_price=price)
        r=dispatch(list(m.assets.values()),{},day_intervals(date(2026,11,5),30)[:1],
            [40],[100],{'a':[25],'b':[25]},{p:[0] for p in PRODUCTS},m.ruleset.to_dict(),
            res_forecasts={'a':[50],'b':[50]})
        rows=r['rows'][0]['assets']
        self.assertAlmostEqual(rows['b']['down_mwh'],25)
        self.assertAlmostEqual(rows['a']['down_mwh'],5)
        self.assertAlmostEqual(r['fixed_lp_objective_eur'],225)
        self.assertAlmostEqual(r['rows'][0]['price_eur_mwh'],-20)
        for row in rows.values():
            self.assertEqual(row['up_mwh'],0)
            self.assertEqual(sum(row['reserve_mw'].values()),0)

    def test_slack_and_RES_eligibility_validation(self):
        for config in ({'energy_shortage_eur_mwh':0},{'energy_surplus_eur_mwh':float('nan')},
                       {'reserve_shortage_eur_mw_mtu':{'FCR_up':-1}}):
            with self.assertRaises(ValueError):Ruleset({'slack':config})
        with self.assertRaises(NotImplementedError):Ruleset({'res':{'participation_balancing_capacity':True}})

    def test_fixture_RTBM_RES_bid_is_price_forming(self):
        from emst.rtbm import clear_rtu
        from emst.settlement import period_price
        interval=day_intervals(date(2026,11,5),5)[0]
        result=clear_rtu(interval,[],{},{},{},40,100,40,100,delivery_equals_dispatch=True,
            res_energy_offers=[dict(id='res',participant='solar',available_mwh=100/12,commercial_mwh=100/12,down_price=-5)])
        self.assertAlmostEqual(result['res_offer_rows'][0]['down_mwh'],5)
        self.assertAlmostEqual(result['entity_down']['res'],5)
        self.assertAlmostEqual(result['res_actual_mwh'],40/12)
        price=period_price([result]*6,[[80]*6 for _ in range(3)])
        self.assertAlmostEqual(price['price_eur_mwh'],-5)
        self.assertFalse(price['historical_fallback'])


@unittest.skipUnless(highspy is not None,'Full RES daily integration requires HiGHS audit extra')
class RESDailyTests(unittest.TestCase):
    def run_day(self,*,bid_demand=100,actual_kind='available',actual_RES=100):
        m=Market();m.add_participant('solar_owner');m.add_participant('supplier')
        m.add_res('solar','solar_owner',generation_forecast=[100]*48,actual_generation=[actual_RES]*48,
            capacity_mw=100,energy_down_price=-5,actual_generation_kind=actual_kind)
        m.add_supplier('load','supplier',demand_forecast=[bid_demand]*48,actual_consumption=[40]*48)
        return m.run(time_index=m.time_index('2026-11-05'),system_forecasts={'demand':[40]*48,'res':[100]*48},
            historical_rtbm_prices={t:[[80]*6 for _ in range(3)] for t in range(48)})

    def test_instructed_RES_energy_is_paid_and_not_weather_imbalance(self):
        result=self.run_day()
        self.assertTrue(result.audit['passed'],result.audit)
        self.assertTrue(result.manifest['physical_security_feasible'])
        self.assertAlmostEqual(result.manifest['physical_imbalance_mwh'],0,places=5)
        self.assertTrue(all(abs(r['assets']['solar']['down_mwh']-5)<1e-5 for r in result.rtbm))
        self.assertTrue(all(abs(r['price_eur_mwh']+5)<1e-5 for r in result.rtbm))
        self.assertTrue(all(abs(r['system_prediction']['res_prediction_mw']-100)<1e-5 for r in result.rtbm))
        meters=[r for r in result.meters if r['asset']=='solar']
        self.assertTrue(all(abs(r['INSTQ_mwh']+30)<1e-5 and abs(r['UNINSTQ_mwh'])<1e-5 for r in meters))
        cash=sum(float(e['cash_eur']) for e in result.ledger if e['component']=='S03' and e['account']=='participant:solar_owner:nonDAM')
        self.assertAlmostEqual(cash,7200,places=4)

    def test_nonzero_RES_tolerance_in_public_settlement(self):
        from emst.settlement import res_bridge
        m=Market(ruleset={'settlement':{'res_tolerance':{'commercial':.02}}})
        m.add_participant('solar_owner');m.add_participant('supplier')
        m.add_res('solar','solar_owner',generation_forecast=[100]*48,actual_generation=[80]*48,
            capacity_mw=100,energy_down_price=-5)
        m.add_supplier('load','supplier',demand_forecast=[100]*48,actual_consumption=[40]*48)
        result=m.run(time_index=m.time_index('2026-11-05'),system_forecasts={'demand':[40]*48,'res':[100]*48},
            historical_rtbm_prices={t:[[80]*6 for _ in range(3)] for t in range(48)})
        self.assertTrue(result.audit['passed'],result.audit)
        changed=False
        for row in result.meters:
            if row['asset']!='solar':continue
            down=sum(r['RES_delivered_down_mwh']['solar'] for r in result.rtbm[row['period']*6:(row['period']+1)*6])
            expected=res_bridge(row['MSQ_mwh'],down,row['actual_mwh'],m.ruleset['settlement'])
            self.assertAlmostEqual(row['INSTQ_mwh'],expected['INSTQ'])
            self.assertAlmostEqual(row['UNINSTQ_mwh'],expected['UNINSTQ'])
            changed |= abs(row['INSTQ_mwh']+down)>1e-6
        self.assertTrue(changed)

    def test_metered_input_is_not_curtailed_twice(self):
        result=self.run_day(actual_kind='metered',actual_RES=40)
        self.assertTrue(result.audit['passed'],result.audit)
        self.assertTrue(all(abs(r['actual_RES_mwh']-40/12)<1e-5 for r in result.rtbm))

    def test_weather_shortfall_remains_uninstructed(self):
        result=self.run_day(actual_RES=80)
        self.assertTrue(result.audit['passed'],result.audit)
        meters=[m for m in result.meters if m['asset']=='solar']
        self.assertTrue(all(abs(m['UNINSTQ_mwh']+10)<1e-5 for m in meters))
        self.assertTrue(all(abs(m['INSTQ_mwh']+sum(r['RES_delivered_down_mwh']['solar'] for r in result.rtbm[m['period']*6:(m['period']+1)*6]))<1e-5 for m in meters))

    def test_unaccepted_DAM_generation_is_not_paid_as_balancing_curtailment(self):
        result=self.run_day(bid_demand=40)
        self.assertTrue(result.audit['passed'],result.audit)
        self.assertTrue(all(abs(r['assets']['solar']['down_mwh'])<1e-6 for r in result.rtbm))
        self.assertTrue(all(abs(r['res_commercial_curtailment_mw']-60)<1e-5 for r in result.rtbm))
        self.assertTrue(all(abs(r['actual_RES_mwh']-40/12)<1e-5 for r in result.rtbm))
        self.assertTrue(all(abs(float(e['cash_eur']))<1e-5 for e in result.ledger if e['component']=='S03' and e['account']=='participant:solar_owner:nonDAM'))


if __name__=='__main__':unittest.main()

import unittest
from datetime import date
import pulp as lp
from emst import Market
from emst.system import observation_update, reserve_series, system_series
from emst.calendar import day_intervals
from emst.physics import thermal_path
from emst.optimization import solve, highspy


class SystemInputsTests(unittest.TestCase):
    def test_persist_error_against_baseline_not_previous_corrected_prediction(self):
        previous=dict(demand_mw=115,res_mw=25,forecast_demand_mw=100,forecast_res_mw=30)
        r=observation_update(110,20,previous=previous)
        self.assertEqual(r['demand_prediction_mw'],125)
        self.assertEqual(r['res_prediction_mw'],15)
        self.assertEqual(r['net_demand_prediction_mw'],110)
        self.assertEqual(observation_update(100,20)['net_demand_prediction_mw'],80)
        previous['forecast_res_mw']=100
        self.assertEqual(observation_update(110,20,previous=previous)['res_prediction_mw'],0)

    def test_directional_requirements_and_duplicate_rejection(self):
        index=Market.time_index('2026-11-05')
        r=reserve_series({'FCR':{'up':[2]*48,'down':[3]*48},'aFRR':{'up':[4]*48,'down':[5]*48},'mFRR':{'up':[6]*48,'down':[7]*48}},index)
        self.assertEqual(r['mFRR_up'],[6]*48)
        self.assertEqual(r['FCR_down'],[3]*48)
        for bad in ({'FCR':{'up':[1]*48}}, {'mFRR_up':[-1]*48}, {'FFR_up':[1]*48},
                    {'FCR':{'up':[1]*48,'down':[0]*48},'FCR_up':[1]*48}):
            with self.assertRaises(ValueError):reserve_series(bad,index)

    def test_system_forecasts_have_independent_five_minute_shape(self):
        index=Market.time_index('2026-11-05');rtus=[x.start for x in day_intervals(date(2026,11,5),5)]
        mtu,rtu,source=system_series({'demand':list(range(288)),'res':[0]*288},index,rtus,name='TSO')
        self.assertEqual(mtu['demand'][0],2.5)
        self.assertEqual(rtu['demand'][5],5)
        self.assertEqual(source,'explicit_system_input')
        with self.assertRaises(ValueError):system_series({'demand':[1]*48},index,rtus,name='TSO')

    def test_shared_thermal_kernel_exact_ramp_energy(self):
        m=lp.LpProblem('ramp_integral',lp.LpMaximize)
        nodes,energy=thermal_path(m,'u',0,1/12,10,100,1,0,120)
        m+=energy;solve(m,False)
        self.assertAlmostEqual(nodes[-1].value(),10,places=5)
        self.assertAlmostEqual(lp.value(energy),.5*10/12,places=6)

    def test_public_and_fixture_adapters_share_thermal_energy_and_prices(self):
        from emst import ThermalState
        from emst.model import Unit,PRODUCTS
        from emst.dispatch import dispatch
        from emst.rtbm import clear_rtu
        from emst.isp import schedule
        from datetime import datetime,timezone
        market=Market();market.add_participant('p')
        asset=market.add_thermal('u','p',capacity_mw=50,minimum_mw=0,marginal_cost=50,
            ramp_mw_hour=600,reserve_limit_mw=30,initial_state=ThermalState('online',20,180))
        state={'u':dict(on=1,stage='online',power_mw=20,duration_minutes=180)}
        unit=Unit('u','p',50,0,50,600,20,1,6,no_load_eur_period=0)
        periods=day_intervals(date(2026,11,5),30)[:1]
        req={p:[1 if p=='FCR_up' else 0] for p in PRODUCTS}
        public=dispatch([asset],state,periods,[25],[0],{'u':[12.5]},req,market.ruleset.to_dict())
        fixture=schedule([unit],periods,[25],[0],{'u':[12.5]},req,
            {'u':dict(on=1,end_mw=20,duration_periods=6)},datetime.now(timezone.utc),'shared_ISP_test')
        self.assertAlmostEqual(public['rows'][0]['assets']['u']['power_mw'],fixture['rows'][0]['power_mw'],places=5)
        self.assertAlmostEqual(public['rows'][0]['reserve_prices']['FCR_up'],fixture['rows'][0]['reserve_price_eur_mw_period']['FCR_up'],places=5)
        rtu=day_intervals(date(2026,11,5),5)[0]
        reference={'u':dict(on=1,agc=1,reserve_mw={p:0 for p in PRODUCTS})}
        rules=market.ruleset.to_dict();rules['schedule_deviation_cost_eur_mwh']=5
        public=dispatch([asset],state,[rtu],[30],[0],{'u':[20/12]},
            {p:[0] for p in PRODUCTS},rules,commitment={'u':1},reference=reference,rtbm=True)
        fixture=clear_rtu(rtu,[unit],{'u':dict(on=1,end_mw=20,duration_rtus=36)},reference,
            {'u':20/12},30,0,30,0,delivery_equals_dispatch=True)
        self.assertAlmostEqual(public['rows'][0]['assets']['u']['energy_mwh'],fixture['rows'][0]['instructed_energy_mwh'],places=5)
        self.assertAlmostEqual(public['rows'][0]['assets']['u']['end_mw'],fixture['rows'][0]['instructed_terminal_mw'],places=4)
        self.assertAlmostEqual(public['rows'][0]['price_eur_mwh'],fixture['price_eur_mwh'],places=4)


@unittest.skipUnless(highspy is not None,'Full-day integration requires HiGHS audit extra')
class SystemDailyTests(unittest.TestCase):
    def test_separate_TSO_forecasts_and_causal_measurement_spike(self):
        from test_public_api import example,HISTORY
        def run(spike):
            market=example(100,20)
            observation=[100.]*288
            observation[20]=spike
            return market.run(time_index=Market.time_index('2026-11-05'),
                system_forecasts={'demand':[105]*48,'res':[22]*48},
                system_observations={'demand':observation,'res':[20]*288},
                reserve_requirements={'FCR':{'up':[1]*48,'down':[1]*48}},historical_rtbm_prices=HISTORY)
        base=run(100);changed=run(108)
        self.assertTrue(base.audit['passed'],base.audit)
        self.assertTrue(changed.audit['passed'],changed.audit)
        self.assertEqual(base.dam,changed.dam)
        self.assertEqual(base.inputs['forecast_mw']['factory_load'],[100.]*48)
        self.assertEqual(changed.inputs['system_forecast_mw']['demand'],[105.]*288)
        def dispatch_view(row):return {k:v for k,v in row.items() if k not in ('settlement_price_eur_mwh','settlement_price_source')}
        self.assertEqual([dispatch_view(r) for r in base.rtbm[:20]],[dispatch_view(r) for r in changed.rtbm[:20]])
        self.assertAlmostEqual(sum(x['power_mw'] for x in base.isp_runs[0]['rows'][0]['assets'].values()),105)
        self.assertEqual(changed.rtbm[20]['assets'],base.rtbm[20]['assets'])
        self.assertAlmostEqual(changed.rtbm[21]['system_prediction']['net_demand_prediction_mw']-base.rtbm[21]['system_prediction']['net_demand_prediction_mw'],8)
        self.assertGreater(sum(x['energy_mwh'] for x in changed.rtbm[21]['assets'].values()),sum(x['energy_mwh'] for x in base.rtbm[21]['assets'].values()))
        self.assertEqual(changed.rtbm[22]['system_prediction'],base.rtbm[22]['system_prediction'])


if __name__=='__main__':unittest.main()

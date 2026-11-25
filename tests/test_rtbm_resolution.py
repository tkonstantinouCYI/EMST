from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
import json,tempfile,unittest
from emst import Market,ThermalState
from emst.resolution import history_sets
from emst.optimization import highspy
from tests_support import without_runtime

HISTORY={t:[[80.+i for i in range(6)] for _ in range(3)] for t in range(48)}


def market(actual=None):
    m=Market();m.add_participant('generator');m.add_participant('supplier')
    m.add_thermal('unit','generator',capacity_mw=100,minimum_mw=10,marginal_cost=60,
        initial_state=ThermalState('dispatch',40,480))
    m.add_supplier('load','supplier',demand_forecast=[40.]*48,
        actual_consumption=actual if actual is not None else [40.]*288)
    return m


class ResolutionValidationTests(unittest.TestCase):
    def test_invalid_interval_and_history_shapes(self):
        for value in (0,15,60,True,30.,'30'):
            with self.assertRaisesRegex(ValueError,'rtbm_interval_minutes'):
                market().run(time_index=Market.time_index('2026-11-05'),rtbm_interval_minutes=value)
        self.assertEqual(history_sets([[1,2,3,4,5,6]]*3,1),[[3.5]]*3)
        self.assertEqual(history_sets([[2]]*3,1),[[2]]*3)
        with self.assertRaises(ValueError):history_sets([[2]]*3,6)


@unittest.skipUnless(highspy is not None,'Integration requires HiGHS')
class ResolutionIntegrationTests(unittest.TestCase):
    def test_coarse_uses_current_interval_mean_and_immediate_power(self):
        actual=[40.]*288
        actual[0]=52.  # The first completed half-hour mean is 42 MW.
        base=market().run(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,rtbm_interval_minutes=30)
        changed=market(actual).run(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,rtbm_interval_minutes=30)
        self.assertTrue(changed.audit['passed'])
        self.assertEqual(len(changed.rtbm),48)
        self.assertAlmostEqual(changed.rtbm[0]['assets']['unit']['power_mw'],42,places=5)
        self.assertAlmostEqual(changed.rtbm[0]['physical_imbalance_mwh'],0,places=6)
        self.assertAlmostEqual(changed.rtbm[1]['system_prediction']['demand_prediction_mw'],40)
        self.assertAlmostEqual(changed.rtbm[1]['assets']['unit']['power_mw'],40,places=5)
        self.assertEqual(changed.rtbm[1]['system_prediction']['measurement_index'],1)
        self.assertEqual(changed.rtbm[0]['system_observation_available_at'],changed.rtbm[1]['at'])
        self.assertEqual(len(changed.isp_runs[1]['forecast_bridge']),2)
        self.assertTrue(all(r['isp_run']=='ISP_initial' for r in changed.rtbm[:24]))
        self.assertTrue(all(r['isp_run']=='ISP_1100' for r in changed.rtbm[24:]))
        self.assertTrue(changed.manifest['rtbm_research_approximation'])
        self.assertEqual(changed.manifest['ruleset']['rtbm_period_minutes'],5)
        self.assertEqual(len(changed.inputs['actual_mw']['load']),48)
        self.assertEqual(len(changed.inputs['source_actual_mw_5min']['load']),288)
        self.assertAlmostEqual(sum(m['actual_mwh'] for m in changed.meters if m['asset']=='load'),sum(actual)/12)
        self.assertAlmostEqual(changed.rtbm[0]['assets']['unit']['trajectory_step_hours'],.5)
        self.assertEqual(len(changed.rtbm[0]['assets']['unit']['nodes_mw']),2)
        report=changed.to_readable(audience='participant',participant='supplier')['simulation_summary']['days'][0]['market_stages']
        self.assertEqual(len(report['rtbm']['summary']['intervals']),48)
        self.assertTrue(report['rtbm']['summary']['research_approximation'])
        self.assertEqual(report['settlement']['summary']['resolution'],'30min_research_approximation')
        self.assertEqual(report['rtbm']['participants']['supplier']['intervals'][0]['market_schedule_mwh'],20)
        self.assertTrue(all(v['reconciled'] for v in changed.economics['participants'].values()))

    def test_noon_ISP_bridge_does_not_read_future_actuals(self):
        values=[40.]*288;values[22*6+1]=52.
        kwargs=dict(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,rtbm_interval_minutes=30)
        base=market().run(**kwargs);changed=market(values).run(**kwargs)
        self.assertEqual(base.isp_runs[1]['forecast_noon_state'],changed.isp_runs[1]['forecast_noon_state'])
        self.assertAlmostEqual(changed.rtbm[22]['assets']['unit']['power_mw'],42,places=5)
        self.assertAlmostEqual(changed.rtbm[23]['system_prediction']['demand_prediction_mw'],40)

    def test_current_RES_and_demand_override_forecasts_with_unlimited_ramp(self):
        m=market([90.]+[40.]*47)
        m.assets['unit'].parameters['ramp_mw_hour']=1.
        m.add_participant('renewable')
        m.add_res('solar','renewable',generation_forecast=[5.]*48,
            actual_generation=[15.]+[5.]*47)
        r=m.run(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,
            rtbm_interval_minutes=30)
        self.assertTrue(r.audit['passed'])
        self.assertAlmostEqual(r.rtbm[0]['system_prediction']['demand_prediction_mw'],90)
        self.assertAlmostEqual(r.rtbm[0]['system_prediction']['res_prediction_mw'],15)
        # All 15 MW of available RES is delivered independently of its schedule;
        # thermal output meets the remainder with immediate 30-minute ramping.
        self.assertGreater(r.rtbm[0]['assets']['unit']['power_mw'],70)
        self.assertAlmostEqual(r.rtbm[0]['physical_imbalance_mwh'],0,places=5)
        self.assertEqual(r.rtbm[0]['assets']['unit']['ramping_mode'],'immediate')

    def test_default_is_identical_to_explicit_five_minutes(self):
        kwargs=dict(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY)
        default=market().run(**kwargs)
        explicit=market().run(**kwargs,rtbm_interval_minutes=5)
        self.assertEqual(without_runtime(asdict(default)),without_runtime(asdict(explicit)))
        self.assertEqual(len(default.rtbm),288)

    def test_thirty_minute_checkpoint_history_and_resume(self):
        scenarios={d:dict(forecasts={'load':[40.]*48},actuals={'load':[41.]*288},
            historical_rtbm_prices=HISTORY,rtbm_interval_minutes=30) for d in ('2026-11-05','2026-11-06')}
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as directory:
            root=Path(directory)
            full=market().run_period(start='2026-11-05',end='2026-11-06',scenario=scenarios,checkpoint_directory=root/'full')
            first=market().run_period(start='2026-11-05',end='2026-11-05',scenario=scenarios,checkpoint_directory=root/'split')
            resumed=market().run_period(start='2026-11-06',end='2026-11-06',scenario=scenarios,checkpoint_directory=root/'split',resume_from=first.checkpoint)
            for day in full.days:
                self.assertEqual(without_runtime(json.loads(json.dumps(asdict(full.days[day])))),
                    without_runtime(json.loads(json.dumps(asdict(resumed.days[day])))))
            state=json.loads(resumed.checkpoint.read_text())['state']
            self.assertEqual(state['rtbm_interval_minutes'],30)
            self.assertEqual(len(state['price_history'][0]['prices']),48)
            self.assertEqual(len(state['bootstrap']['weekday']['0'][0]),1)
            self.assertEqual(full.days['2026-11-06'].inputs['previous_system_observation'],full.days['2026-11-05'].rtbm[-1]['system_observation'])
            changed={'2026-11-07':deepcopy(scenarios['2026-11-06'])};changed['2026-11-07']['rtbm_interval_minutes']=5
            with self.assertRaisesRegex(ValueError,'resolution'):
                market().run_period(start='2026-11-07',end='2026-11-07',scenario=changed,checkpoint_directory=root/'split',resume_from=resumed.checkpoint)

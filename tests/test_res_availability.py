"""Availability is physical; the RES market schedule is a commercial position."""
from dataclasses import asdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json,unittest
from emst import Market
from emst.calendar import day_intervals
from emst.dispatch import dispatch
from emst.model import PRODUCTS,Registration
from emst.settlement import Ledger,res_bridge,settle_energy
from emst.rtbm import clear_rtu
from examples.res_availability_day import scenario,HISTORY
from tests_support import without_runtime

class RESAvailabilityTests(unittest.TestCase):
    def test_acceptance_days_both_RTBM_resolutions_and_hourly_ISP(self):
        for isp,rtbm in ((30,5),(30,30),(60,30)):
            for demand in (100,60):
                with self.subTest(isp=isp,rtbm=rtbm,demand=demand):
                    m,kwargs=scenario(demand,hourly=isp==60)
                    result=m.run(**kwargs,isp_interval_minutes=isp,rtbm_interval_minutes=rtbm)
                    self.assertTrue(result.audit['passed'],result.audit)
                    self.assertFalse(result.manifest['RTBM_slack_used'])
                    self.assertTrue(all(v['reconciled'] for v in result.economics['participants'].values()))
                    for r in result.rtbm:
                        row=r['assets']['solar'];hours=r['hours']
                        self.assertAlmostEqual(row['power_mw'],demand,places=5)
                        self.assertAlmostEqual(row['down_mwh'],(100-demand)*hours,places=5)
                        self.assertAlmostEqual(r['RES_available_mwh']['solar'],r['RES_delivery_mwh']['solar']+r['RES_delivered_down_mwh']['solar'],places=5)
                        self.assertAlmostEqual(row['energy_mwh'],r['RES_delivery_mwh']['solar'],places=5)
                        self.assertAlmostEqual(r['physical_imbalance_mwh'],0,places=5)
                        self.assertAlmostEqual(r['system_observation']['res_mw'],100,places=5)
                    for meter in result.meters:
                        if meter['asset']=='solar':
                            self.assertAlmostEqual(meter['MSQ_mwh'],10)
                            self.assertAlmostEqual(meter['INSTQ_mwh'],-(100-demand)*.5,places=5)
                            self.assertAlmostEqual(meter['UNINSTQ_mwh'],40,places=5)
                    self.assertEqual([len(x['rows']) for x in result.isp_runs],[1440//isp,720//isp])
                    readable=result.to_readable(audience='operator')
                    self.assertIn('simulation_summary',readable)

    def test_negative_commercial_reference_is_not_negative_generation(self):
        m,_=scenario();a=m.assets['solar'];periods=day_intervals(date(2026,11,5),30)[:1]
        for commercial in (10,-10):
            row=dispatch([a],{},periods,[60],[100],{'solar':[commercial]},
                {p:[0] for p in PRODUCTS},m.ruleset.to_dict(),res_forecasts={'solar':[100]})['rows'][0]
            self.assertAlmostEqual(row['assets']['solar']['power_mw'],60)
            self.assertAlmostEqual(row['assets']['solar']['down_mwh'],20)
            b=res_bridge(commercial,20,30)
            self.assertEqual(b['INSTQ'],-20)
            self.assertEqual(b['UNINSTQ'],50-commercial)

    def test_specialised_dispatch_and_settlement_above_schedule(self):
        period=day_intervals(date(2026,11,5),30)[0]
        r=clear_rtu(period,[],{},{},{},60,100,60,100,delivery_equals_dispatch=True,
            res_energy_offers=[dict(id='res',participant='solar_owner',available_mwh=50,commercial_mwh=10,down_price=-5)])
        self.assertAlmostEqual(r['res_offer_rows'][0]['down_mwh'],20)
        self.assertAlmostEqual(r['res_actual_mwh'],30)
        reg=Registration('res','solar_owner','brp','member','meter','production','HV',tolerance=0)
        r['entity_meters']={'res':30}
        bridges,_=settle_energy(Ledger(),[period],[r],{'res':reg},{'res':[10]},
            {0:[[80]*6 for _ in range(3)]})
        self.assertAlmostEqual(bridges[0]['INSTQ'],-20)
        self.assertAlmostEqual(bridges[0]['UNINSTQ'],40)

    def test_metered_delivery_is_fixed_and_known_curtailment_is_added_back(self):
        for known in (None,[40.]*48):
            m,kwargs=scenario(60,kind='metered',output=60,known_curtailment=known)
            r=m.run(**kwargs,rtbm_interval_minutes=30)
            self.assertTrue(r.audit['passed'],r.audit)
            for row in r.rtbm:
                self.assertAlmostEqual(row['assets']['solar']['power_mw'],60)
                self.assertAlmostEqual(row['actual_RES_mwh'],30)
                self.assertAlmostEqual(row['assets']['solar']['down_mwh'],0 if known is None else 20)
                self.assertAlmostEqual(row['system_observation']['res_mw'],60 if known is None else 100)
            for meter in r.meters:
                if meter['asset']=='solar':
                    self.assertAlmostEqual(meter['INSTQ_mwh'],0 if known is None else -20)
                    self.assertAlmostEqual(meter['UNINSTQ_mwh'],20 if known is None else 40)

    def test_mixed_metered_and_available_portfolios_keep_their_own_baselines(self):
        m,kwargs=scenario(100)
        m.add_participant('meter_owner')
        m.add_res('meter','meter_owner',generation_forecast=[40.]*48,actual_generation=[40.]*48,
            actual_generation_kind='metered',capacity_mw=40)
        r=m.run(**kwargs,rtbm_interval_minutes=30)
        self.assertTrue(r.audit['passed'],r.audit)
        for row in r.rtbm:
            self.assertAlmostEqual(row['assets']['meter']['power_mw'],40)
            self.assertAlmostEqual(row['assets']['meter']['down_mwh'],0)
            self.assertAlmostEqual(row['assets']['solar']['power_mw'],60)
            self.assertAlmostEqual(row['assets']['solar']['down_mwh'],20)

    def test_input_validation(self):
        with self.assertRaisesRegex(ValueError,'requires metered'):
            scenario(60,known_curtailment=[40.]*48)
        for control in ([-1.]*48,[float('nan')]*48,[50.]*48,[40.]*47):
            m,kwargs=scenario(60,kind='metered',output=60,known_curtailment=control)
            with self.assertRaises(ValueError):m.run(**kwargs,rtbm_interval_minutes=30)

    def test_checkpoint_resume_both_ISP_grids(self):
        for isp,rtbm in ((30,5),(30,30),(60,30)):
            m,_=scenario(60,hourly=isp==60)
            data={day:dict(forecasts={'solar':[100.]*48,'load':[20.]*48},
                actuals={'solar':[100.]*48,'load':[60.]*48},
                system_forecasts={'demand':[60.]*48,'res':[100.]*48},
                historical_rtbm_prices=HISTORY,isp_interval_minutes=isp,rtbm_interval_minutes=rtbm)
                for day in ('2026-11-05','2026-11-06')}
            with TemporaryDirectory() as folder:
                root=Path(folder)
                full=m.run_period(start='2026-11-05',end='2026-11-06',scenario=data,checkpoint_directory=root/'full')
                first=m.run_period(start='2026-11-05',end='2026-11-05',scenario=data,checkpoint_directory=root/'split')
                resumed=m.run_period(start='2026-11-06',end='2026-11-06',scenario=data,checkpoint_directory=root/'split',resume_from=first.checkpoint)
                for day in full.days:
                    self.assertEqual(without_runtime(json.loads(json.dumps(asdict(full.days[day])))),without_runtime(json.loads(json.dumps(asdict(resumed.days[day])))))
                    self.assertTrue(full.days[day].audit['passed'])

    def test_metered_excess_is_surplus_not_new_curtailment(self):
        m,kwargs=scenario(60,kind='metered',output=100)
        result=m.run(**kwargs,rtbm_interval_minutes=30)
        self.assertTrue(result.audit['passed'],result.audit)
        for r in result.rtbm:
            self.assertAlmostEqual(r['assets']['solar']['power_mw'],100)
            self.assertAlmostEqual(r['assets']['solar']['down_mwh'],0)
            self.assertAlmostEqual(r['slack']['energy_surplus_mwh'],20)

    def test_noon_projection_never_reads_future_RES_actuals(self):
        m,kwargs=scenario(100,hourly=True)
        first=m.run(**kwargs,isp_interval_minutes=60,rtbm_interval_minutes=30)
        m.assets['solar'].actual=[100.]*23+[80.]*25
        second=m.run(**kwargs,isp_interval_minutes=60,rtbm_interval_minutes=30)
        self.assertEqual(first.isp_runs[1]['forecast_noon_state'],second.isp_runs[1]['forecast_noon_state'])
        self.assertEqual(first.rtbm[22]['assets']['solar'],second.rtbm[22]['assets']['solar'])
        self.assertAlmostEqual(second.rtbm[23]['assets']['solar']['power_mw'],80)
        self.assertAlmostEqual(second.rtbm[23]['system_prediction']['res_prediction_mw'],80)

    def test_daily_metered_controls_are_not_silently_carried_forward(self):
        m,_=scenario(60,kind='metered',output=60)
        data={day:dict(forecasts={'solar':[100.]*48,'load':[20.]*48},
            actuals={'solar':[60.]*48,'load':[60.]*48},historical_rtbm_prices=HISTORY,
            rtbm_interval_minutes=30) for day in ('2026-11-05','2026-11-06')}
        data['2026-11-05']['metered_curtailment_mw']={'solar':[40.]*48}
        with TemporaryDirectory() as folder:
            result=m.run_period(start='2026-11-05',end='2026-11-06',scenario=data,checkpoint_directory=folder)
            self.assertAlmostEqual(result.days['2026-11-05'].rtbm[-1]['RES_delivered_down_mwh']['solar'],20)
            self.assertAlmostEqual(result.days['2026-11-06'].rtbm[0]['RES_delivered_down_mwh']['solar'],0)
            self.assertIsNone(m.assets['solar'].metered_curtailment)

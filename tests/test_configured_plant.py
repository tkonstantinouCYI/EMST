from copy import deepcopy
from dataclasses import asdict
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from emst import Market,PlantState
from emst.configurations import states,boundary,validate
from emst.dispatch import dispatch
from emst.calendar import day_intervals
from emst.model import PRODUCTS
from emst.solver import SolveError
from datetime import date
from examples.configured_plant import build_market
from tests_support import without_runtime

HISTORY={t:[[60.]*6 for _ in range(3)] for t in range(48)}


def force_state(model,state,index=0):
    import emst.dispatch as module
    original=module.solve
    names=list(states(model.assets['ccgt'].parameters['plant_configurations']))
    def forced(problem,mip=True):
        if mip:problem+=problem._emst_configuration_states['ccgt'][state,index]==1
        return original(problem,mip)
    return patch.object(module,'solve',side_effect=forced)


def isp(m,demand,requirements=None):
    a=m.assets['ccgt'];intervals=day_intervals(date(2026,11,5),30)[:len(demand)]
    return dispatch([a],{'ccgt':boundary(a.initial_state)},intervals,demand,[0.]*len(demand),
        {'ccgt':[d*.5 for d in demand]},requirements or {p:[0.]*len(demand) for p in PRODUCTS},m.ruleset.to_dict())


class ConfiguredPlantTests(unittest.TestCase):
    formulation='reference'

    def setUp(self):
        original=build_market
        def selected(*args,**kwargs):
            market=original(*args,**kwargs)
            market.assets['ccgt'].parameters['configuration_formulation']=self.formulation
            return market
        self.selection=patch(__name__+'.build_market',side_effect=selected)
        self.selection.start();self.addCleanup(self.selection.stop)

    def test_invalid_component_history_and_transition_data(self):
        a=build_market().assets['ccgt'];d=a.parameters['plant_configurations']
        invalid=asdict(a.initial_state);invalid['component_histories']['GT2']['on']=True
        with self.assertRaisesRegex(ValueError,'histories inconsistent'):validate(d['components'],d['configurations'],d['transitions'],invalid)
        bad=deepcopy(d['transitions']);bad[0]['components_by_step']=[[]]
        with self.assertRaises(ValueError):validate(d['components'],d['configurations'],bad,a.initial_state)
        bad=deepcopy(d['transitions']);bad[0]['profile_mw'][-1]=1000
        with self.assertRaises(ValueError):validate(d['components'],d['configurations'],bad,a.initial_state)

    def test_mutual_exclusivity_and_no_configuration_capacity_addition(self):
        m=build_market()
        with force_state(m,'GT1_ST'):
            r=isp(m,[100.])
        x=r['rows'][0]['assets']['ccgt']
        self.assertAlmostEqual(x['power_mw'],80)
        self.assertAlmostEqual(r['rows'][0]['slack']['energy_shortage_mwh'],10)
        self.assertEqual(sum(x['configuration_binaries'].values()),1)
        self.assertLessEqual(x['power_mw'],80)
        self.assertEqual(set(m.assets),{'ccgt','backup','load'})

    def test_component_minimum_down_time_prevents_incremental_start(self):
        m=build_market();a=m.assets['ccgt'];h=deepcopy(a.initial_state.component_histories);h['GT2']['duration_minutes']=0
        a.initial_state=PlantState('GT1_ST',60,h)
        with force_state(m,'add2:0'):
            with self.assertRaises(SolveError):isp(m,[80.,110.])

    def test_component_minimum_up_time_survives_configuration_change(self):
        m=build_market();a=m.assets['ccgt'];h={c:dict(on=True,duration_minutes=600) for c in ('GT1','GT2','ST')};h['GT1']['duration_minutes']=0
        a.initial_state=PlantState('GT1_GT2_ST',120,h)
        with force_state(m,'drop1:0'):
            with self.assertRaises(SolveError):isp(m,[100.,70.])

    def test_illegal_direct_configuration_jump_is_infeasible(self):
        m=build_market()
        with force_state(m,'GT2_ST'):
            with self.assertRaises(SolveError):isp(m,[60.])

    def test_configuration_reserve_headroom_and_transition_ineligibility(self):
        m=build_market();requirements={p:[0.] for p in PRODUCTS};requirements['FCR_up']=[1.]
        with force_state(m,'GT1_ST'):
            r=isp(m,[80.],requirements)
        x=r['rows'][0]['assets']['ccgt']
        self.assertLessEqual(x['power_mw']+x['reserve_mw']['FCR_up'],80+1e-6)
        self.assertAlmostEqual(x['reserve_mw']['FCR_up']+r['rows'][0]['slack']['reserve_shortage_mw']['FCR_up'],1)
        with force_state(m,'add2:0'):
            r=isp(m,[80.],requirements)
        self.assertTrue(all(abs(v)<1e-7 for v in r['rows'][0]['assets']['ccgt']['reserve_mw'].values()))

    def test_both_RTBM_modes_follow_ISP_and_settle_one_plant(self):
        for minutes in (5,30):
            with self.subTest(minutes=minutes):
                m=build_market();r=m.run(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,rtbm_interval_minutes=minutes)
                self.assertTrue(r.audit['passed']);self.assertTrue(r.manifest['physical_security_feasible'])
                self.assertEqual(sum(x['asset']=='ccgt' for x in r.meters),48)
                self.assertFalse(any(o['asset'] in ('GT1_ST','GT2_ST','GT1_GT2_ST') for o in r.orders))
                self.assertAlmostEqual(sum(v['assets']['ccgt']['transition_cost_eur'] for v in r.rtbm),250)
                costs=r.economics['assets']['ccgt']['physical_costs']['totals']
                self.assertAlmostEqual(costs['transition_cost_eur'],250);self.assertEqual(costs['startup_cost_eur'],0)
                self.assertTrue(all(v['reconciled'] for v in r.economics['participants'].values()))
                self.assertEqual(r.ending_states['ccgt']['component_histories']['GT1']['duration_minutes'],2040)
                transition=[v['assets']['ccgt'] for v in r.rtbm if v['assets']['ccgt']['configuration_transition']]
                self.assertEqual(len(transition),60//minutes)
                if minutes==30:self.assertNotAlmostEqual(transition[0]['power_mw'],transition[0]['end_mw'])
                report=r.to_readable(audience='participant',participant='plant_owner')
                stages=report['simulation_summary']['days'][0]['market_stages']
                self.assertIn('configuration_state',stages['rtbm']['participants']['plant_owner']['intervals'][0])

    def test_transition_crosses_noon_without_restart_in_both_modes(self):
        for minutes in (5,30):
            with self.subTest(minutes=minutes):
                m=build_market([40.]*22+[60.,80.]+[130.]*24)
                e=next(e for e in m.assets['ccgt'].parameters['plant_configurations']['transitions'] if e['id']=='add2')
                e['profile_mw'].append(140.);e['components_by_step'].append(['GT1','GT2','ST'])
                r=m.run(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,rtbm_interval_minutes=minutes)
                self.assertTrue(r.audit['passed'])
                self.assertEqual(r.isp_runs[1]['forecast_noon_state']['ccgt']['transition'],{'id':'add2','elapsed_minutes':60})
                self.assertEqual(r.rtbm[24*(30//minutes)]['assets']['ccgt']['configuration_state'],'add2:2')
                self.assertAlmostEqual(sum(v['assets']['ccgt']['transition_cost_eur'] for v in r.rtbm),250)
                self.assertAlmostEqual(r.isp_runs[1]['objective_components_eur']['configuration_transition_cost'],0)

    def test_back_to_back_transitions_have_no_invented_stable_MTU(self):
        m=build_market()
        with force_state(m,'add2:0',0),force_state(m,'drop2:0',2):
            r=isp(m,[80.,110.,100.,70.])
        rows=[v['assets']['ccgt'] for v in r['rows']]
        self.assertEqual([v['configuration_state'] for v in rows],['add2:0','add2:1','drop2:0','drop2:1'])
        self.assertAlmostEqual(sum(v['transition_cost_eur'] for v in rows),350)
        self.assertTrue(all(v['slack']['energy_shortage_mwh']<1e-6 and v['slack']['energy_surplus_mwh']<1e-6 for v in r['rows']))

    def test_explicit_transition_operating_costs_are_reconciled(self):
        m=build_market();e=next(e for e in m.assets['ccgt'].parameters['plant_configurations']['transitions'] if e['id']=='add2')
        e['marginal_cost']=75.;e['no_load_cost_per_hour']=100.
        with force_state(m,'add2:0'):
            r=isp(m,[80.])
        self.assertAlmostEqual(r['objective_components_eur']['configuration_operating_adjustment'],850)
        self.assertAlmostEqual(r['objective_components_eur']['configuration_transition_cost'],250)
        self.assertAlmostEqual(r['milp_objective_eur'],1100)
        self.assertAlmostEqual(r['rows'][0]['assets']['ccgt']['up_offer_eur_mwh'],75)

    def test_RTBM_cannot_jump_to_another_stable_configuration(self):
        m=build_market();a=m.assets['ccgt']
        with self.assertRaisesRegex(ValueError,'permitted transition'):
            dispatch([a],{'ccgt':boundary(a.initial_state)},day_intervals(date(2026,11,5),5)[:1],
                [60.],[0.],{'ccgt':[5.]},{},m.ruleset.to_dict(),rtbm=True,
                reference={'ccgt':{'configuration_state':'GT2_ST'}})

    def test_unfinished_transition_and_component_clocks_checkpoint_resume_both_modes(self):
        for minutes in (5,30):
            with self.subTest(minutes=minutes):
                m=build_market();e=next(e for e in m.assets['ccgt'].parameters['plant_configurations']['transitions'] if e['id']=='add2')
                e['profile_mw'].append(140.);e['components_by_step'].append(['GT1','GT2','ST'])
                scenarios={d:dict(forecasts={'load':load},actuals={'load':load},historical_rtbm_prices=HISTORY,rtbm_interval_minutes=minutes) for d,load in (
                    ('2026-11-05',[100.]*47+[120.]),('2026-11-06',[170.]*48))}
                with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as directory:
                    root=Path(directory)
                    full=m.run_period(start='2026-11-05',end='2026-11-06',scenario=scenarios,checkpoint_directory=root/'full')
                    first=m.run_period(start='2026-11-05',end='2026-11-05',scenario=scenarios,checkpoint_directory=root/'split')
                    end=first.days['2026-11-05'].ending_states['ccgt']
                    self.assertEqual(end['transition'],{'id':'add2','elapsed_minutes':60})
                    self.assertEqual(end['component_histories']['GT2']['duration_minutes'],60)
                    resumed=m.run_period(start='2026-11-06',end='2026-11-06',scenario=scenarios,checkpoint_directory=root/'split',resume_from=first.checkpoint)
                    for day in full.days:
                        self.assertEqual(without_runtime(json.loads(json.dumps(asdict(full.days[day])))),without_runtime(json.loads(json.dumps(asdict(resumed.days[day])))))
                        self.assertTrue(full.days[day].audit['passed'])
                    second=full.days['2026-11-06']
                    self.assertEqual(second.rtbm[0]['assets']['ccgt']['configuration_state'],'add2:2')
                    self.assertEqual(sum(v['assets']['ccgt']['transition_cost_eur'] for v in second.rtbm),0)

class CompactConfiguredPlantTests(ConfiguredPlantTests):
    formulation='compact'


if __name__=='__main__':unittest.main()

"""Warm hints are repairable suggestions, never market commitment constraints."""
import unittest
from copy import deepcopy
from datetime import date
from unittest.mock import patch
import pulp as lp
from emst.calendar import day_intervals
from emst.dispatch import dispatch
from emst.configurations import boundary
from emst.model import PRODUCTS
from emst.optimization import highspy
from emst.solver import session,configuration,SolveError
from emst.warm_start import prepare,validate_seed
from examples.configured_plant import build_market


@unittest.skipUnless(highspy is not None,'Requires optional HiGHS audit extra')
class WarmStartTests(unittest.TestCase):
    def test_repair_changes_illegal_commitment_without_mutating_main_model(self):
        m=build_market(configuration_formulation='compact');a=m.assets['ccgt']
        original=__import__('emst.dispatch',fromlist=['solve']).solve
        captured={}
        def take(problem,mip=True):
            if mip:
                captured['warm']=deepcopy(problem._emst_warm_start)
                self.assertTrue(captured['warm']['candidate_available'])
                self.assertGreater(captured['warm']['changed_binary_count'],0)
                self.assertEqual(captured['warm']['repair_attempts'][0]['status'],'Infeasible')
            return original(problem,mip)
        seed={'rows':[dict(period=0,assets={'ccgt':{'configuration_state':'OFF'}})]}
        import emst.dispatch as module
        with session({'warm_start':True}),patch.object(module,'solve',side_effect=take):
            r=dispatch([a],{'ccgt':boundary(a.initial_state)},day_intervals(date(2026,11,5),30)[:1],
                [80.],[0.],{'ccgt':[40.]},{p:[0.] for p in PRODUCTS},m.ruleset.to_dict(),warm_start_seed=seed)
        self.assertEqual(r['rows'][0]['assets']['ccgt']['configuration_state'],'GT1_ST')

    def test_full_problem_and_gap_survive_repair_and_no_candidate_falls_back(self):
        from emst.optimization import solve
        m=lp.LpProblem('library_ISP',lp.LpMinimize);v=lp.LpVariable('binary',cat='Binary');m+=v;m+=v==1
        def structure():return (v.lowBound,v.upBound,v.cat,str(m.objective),[str(c) for c in m.constraints.values()])
        before=structure()
        with session({'warm_start':True,'warm_start_repair_seconds':1e-12}) as context:
            prepare(m,[],{},[],{})
            self.assertFalse(m._emst_warm_start['candidate_available'])
            self.assertEqual(structure(),before)
            self.assertEqual(solve(m),1)
        self.assertEqual(context['records'][-1]['effective_options']['relative_gap'],0)
        self.assertEqual(context['records'][-1]['effective_options']['absolute_gap'],0)

    def test_unrestricted_MILP_can_improve_a_repaired_commitment(self):
        from emst.optimization import solve
        for backend in ('highs','cbc'):
            if backend=='cbc' and not lp.PULP_CBC_CMD().available():continue
            m=lp.LpProblem('library_ISP',lp.LpMinimize);v=lp.LpVariable('binary',cat='Binary');m+=-10*v
            with session({'warm_start':True,'backend':backend}):
                prepare(m,[],{},[],{})
                self.assertEqual(m._emst_warm_start_values['binary'],0)
                self.assertEqual(solve(m),-10)
            self.assertEqual(v.varValue,1)
            self.assertIn(m._emst_warm_start['submission_status'],('HighsStatus.kOk','submitted_to_CBC_MIP_start'))
        m=lp.LpProblem('library_ISP',lp.LpMinimize);v=lp.LpVariable('zero_price',cat='Binary');m+=0;m+=v>=0
        with session({'warm_start':True}):
            prepare(m,[],{},[],{})
            self.assertEqual(solve(m),0)

    def test_both_runs_use_supplied_seed_then_first_run_and_settle(self):
        history={i:[[60.]*6]*3 for i in range(48)}
        cold=build_market(configuration_formulation='compact').run(time_index=build_market().time_index('2026-11-05'),rtbm_interval_minutes=30,historical_rtbm_prices=history)
        for variant in ('reference','compact'):
            m=build_market(configuration_formulation=variant);m.solver_config=configuration({'warm_start':True})
            r=m.run(time_index=m.time_index('2026-11-05'),rtbm_interval_minutes=30,historical_rtbm_prices=history,isp_warm_start=cold.isp_runs[0])
            self.assertTrue(r.audit['passed']);self.assertTrue(r.manifest['physical_security_feasible'])
            self.assertTrue(all(v['reconciled'] for v in r.economics['participants'].values()))
            for x,y in zip(cold.isp_runs,r.isp_runs):
                self.assertAlmostEqual(x['milp_objective_eur'],y['milp_objective_eur'],places=3)
                self.assertTrue(y['warm_start']['candidate_available'])
                self.assertEqual(y['warm_start']['submission_status'],'HighsStatus.kOk')
                self.assertEqual(y['warm_start']['source'],'provided_ISP_schedule')

    def test_invalid_controls_and_seeds(self):
        for value in (0,-1,True,float('inf')):
            with self.assertRaises(ValueError):configuration({'warm_start_repair_seconds':value})
        with self.assertRaises(ValueError):configuration({'warm_start':'yes'})
        for value in ([],{}, {'rows':[{'period':False,'assets':{}}]}):
            with self.assertRaises(ValueError):validate_seed(value)

    def test_repair_preserves_inherited_transition_actual_power_and_clocks(self):
        from emst import PlantState
        m=build_market(configuration_formulation='compact');a=m.assets['ccgt']
        a.initial_state=PlantState('GT1_ST',107,{c:dict(on=True,duration_minutes=30 if c=='GT2' else 1000) for c in ('GT1','GT2','ST')},dict(id='add2',elapsed_minutes=30))
        with session({'warm_start':True}):
            r=dispatch([a],{'ccgt':boundary(a.initial_state)},day_intervals(date(2026,11,5),30)[:1],
                [113.5],[0.],{'ccgt':[56.75]},{p:[0.] for p in PRODUCTS},m.ruleset.to_dict(),
                warm_start_seed={'rows':[dict(period=0,assets={'ccgt':dict(configuration_state='OFF')})]})
        x=r['rows'][0]['assets']['ccgt']
        self.assertEqual(x['configuration_state'],'add2:1');self.assertAlmostEqual(x['power_mw'],113.5)
        self.assertEqual(x['transition_cost_eur'],0)
        self.assertEqual(r['warm_start']['repair_method'],'free_commitment_MILP')

    def test_storage_SOC_target_forces_repair_of_wrong_charging_hint(self):
        from emst import Market,StorageState
        m=Market(ruleset={'storage':{'participation_balancing_energy':True,'participation_balancing_capacity':False}})
        m.add_participant('owner');a=m.add_storage('battery','owner',power_mw=10,capacity_mwh=10,
            efficiency_charge=1,efficiency_discharge=1,initial_state=StorageState(0,2.5))
        with session({'warm_start':True}):
            r=dispatch([a],{'battery':{'power_mw':0,'soc_mwh':2.5}},day_intervals(date(2026,11,5),30)[:1],
                [5],[0],{'battery':[0]},{p:[0] for p in PRODUCTS},m.ruleset.to_dict(),storage_terminal_soc={'battery':0},
                warm_start_seed={'rows':[dict(period=0,assets={'battery':dict(physical_charging_mode=1)})]})
        x=r['rows'][0]['assets']['battery']
        self.assertAlmostEqual(x['soc_mwh'],0);self.assertAlmostEqual(x['up_mwh'],2.5)
        self.assertEqual(x['physical_charging_mode'],0)
        self.assertEqual(r['warm_start']['repair_method'],'free_commitment_MILP')


if __name__=='__main__':unittest.main()

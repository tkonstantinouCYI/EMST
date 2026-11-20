"""LP suggestions must be repaired, never fixed in final market clearing."""
import unittest
import pulp as lp
from emst.solver import session,configuration
from emst.optimization import solve,highspy
from emst.warm_start import prepare


@unittest.skipUnless(highspy is not None,'Requires HiGHS audit extra')
class LPWarmStartTests(unittest.TestCase):
    def test_fractional_rounding_is_repaired_and_original_problem_unchanged(self):
        m=lp.LpProblem('library_ISP',lp.LpMinimize);v=lp.LpVariable('commit',cat='Binary');m+=-10*v;m+=v<=.6
        before=(v.cat,v.lowBound,v.upBound,str(m.objective),[str(c) for c in m.constraints.values()])
        with session({'warm_start':True,'warm_start_source':'lp'}):
            prepare(m,[],{},[],{})
            w=m._emst_warm_start
            self.assertEqual(w['source'],'LP_relaxation');self.assertEqual(w['LP_fractional_binary_count'],1)
            self.assertAlmostEqual(w['LP_relaxation_objective_eur'],-6)
            self.assertEqual(w['repair_method'],'free_commitment_MILP')
            self.assertEqual(m._emst_warm_start_values['commit'],0)
            self.assertEqual(before,(v.cat,v.lowBound,v.upBound,str(m.objective),[str(c) for c in m.constraints.values()]))
            self.assertEqual(solve(m),0)
    def test_LP_and_schedule_modes_keep_identical_gap_targets(self):
        for source in ('schedule','lp'):
            m=lp.LpProblem('library_ISP',lp.LpMinimize);v=lp.LpVariable('commit',cat='Binary');m+=-10*v
            with session({'warm_start':True,'warm_start_source':source}) as context:
                prepare(m,[],{},[],{});self.assertEqual(solve(m),-10)
            r=context['records'][-1];self.assertEqual(r['effective_options']['relative_gap'],0)
            self.assertEqual(r['effective_options']['absolute_gap'],0)
    def test_source_validation(self):
        with self.assertRaises(ValueError):configuration({'warm_start_source':'round_and_fix'})
    def test_LP_guidance_clears_configured_and_storage_days_both_RTBM_modes(self):
        from examples.configured_plant import build_market as configured
        from examples.library_day import build_market as storage
        for minutes in (5,30):
            for case in ('configured','storage'):
                if case=='configured':
                    m=configured(configuration_formulation='compact')
                    inputs=dict(time_index=m.time_index('2026-11-05'),historical_rtbm_prices={i:[[60.]*6]*3 for i in range(48)})
                else:
                    m,index,reserves,history=storage(storage_energy=True,storage_capacity=True)
                    inputs=dict(time_index=index,reserve_requirements=reserves,historical_rtbm_prices=history,must_run_requirement_mw=[180.]*48)
                m.solver_config=configuration({'stages':{'ISP':{'warm_start':True,'warm_start_source':'lp'}}})
                r=m.run(**inputs,rtbm_interval_minutes=minutes)
                self.assertTrue(r.audit['passed']);self.assertTrue(r.manifest['physical_security_feasible'])
                self.assertTrue(all(v['reconciled'] for v in r.economics['participants'].values()))
                for run in r.isp_runs:
                    self.assertEqual(run['warm_start']['source'],'LP_relaxation')
                    self.assertAlmostEqual(run['milp_objective_eur'],run['fixed_lp_objective_eur'],places=3)


if __name__=='__main__':unittest.main()

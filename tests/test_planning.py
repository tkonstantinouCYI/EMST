from copy import deepcopy
from dataclasses import replace
from decimal import Decimal,localcontext
from pathlib import Path
from unittest.mock import patch
import json,tempfile,unittest
import pulp as lp

from emst import Market,ThermalState,StorageState,Strategy
from emst.evaluation import residuals
from emst.optimization import solve,highspy
from emst.solver import configuration,session,purpose,SolveError
from tests_support import without_runtime

HISTORY={t:[[80.]*6 for _ in range(3)] for t in range(48)}


def market():
    m=Market({'storage':{'participation_balancing_energy':True,'participation_balancing_capacity':True}})
    for p in ('generator','supplier','battery'):m.add_participant(p,economic_owner='portfolio' if p!='supplier' else None)
    m.add_thermal('unit','generator',capacity_mw=100,minimum_mw=20,marginal_cost=60,
        no_load_cost_per_hour=3,startup_cost=7,initial_state=ThermalState('dispatch',50,480))
    m.add_supplier('load','supplier',demand_forecast=[50]*48,actual_consumption=[51]*48)
    m.add_storage('store','battery',power_mw=5,capacity_mwh=10,initial_state=StorageState(0,5),
        degradation_cost=2,strategy_parameters={'buy_mwh':[1 if t==10 else 0 for t in range(48)],
            'sell_mwh':[.9025 if t==30 else 0 for t in range(48)],'balancing_up_price':50})
    return m


class PlanningUnitTests(unittest.TestCase):
    def test_residual_semantics_and_tolerance(self):
        rows=[dict(physical_imbalance_mwh=v,hours=1/12) for v in (1,-1,1e-6)]
        r=residuals(rows)
        self.assertEqual(r['positive_interval_count'],1)
        self.assertEqual(r['negative_interval_count'],1)
        self.assertAlmostEqual(r['net_energy_mwh'],1e-6)
        self.assertEqual(r['maximum_positive_mw'],12)
        self.assertFalse(r['actual_delivered_balance'])
        self.assertFalse(r['reliability_interpretation_available'])
        self.assertIsNone(r['EENS_mwh'])
        self.assertTrue(residuals([dict(physical_imbalance_mwh=1e-6,hours=1/12)])['actual_delivered_balance'])
        self.assertTrue(residuals([dict(physical_imbalance_mwh=0,hours=1/12)])['actual_delivered_balance'])
        for bad in (-1,float('nan'),True):
            with self.assertRaises(ValueError):residuals(rows,bad)

    def test_strict_strategy_parameters_and_copied_input(self):
        m=Market();m.add_participant('p')
        for strategy in (Strategy('marginal_cost',{'markup_eur_mwh':5}),Strategy('fixed_markup',{'markpu':5}),Strategy('fixed_markup',{'markup_eur_mwh':float('nan')})):
            with self.assertRaises(ValueError):m.add_thermal('a','p',capacity_mw=100,minimum_mw=20,marginal_cost=60,initial_state=ThermalState('dispatch',40,480),strategy=strategy)
        params={'markup_eur_mwh':5}
        a=m.add_thermal('a','p',capacity_mw=100,minimum_mw=20,marginal_cost=60,initial_state=ThermalState('dispatch',40,480),strategy=Strategy('fixed_markup',params))
        params['markup_eur_mwh']=99
        self.assertEqual(a.strategy.parameters['markup_eur_mwh'],5)

    def test_solver_configuration_copy_and_failure_contract(self):
        config={'stages':{'ISP':{'time_limit_seconds':2}}}
        m=Market(solver_config=config);config['stages']['ISP']['time_limit_seconds']=99
        self.assertEqual(m.solver_config['stages']['ISP']['time_limit_seconds'],2)
        self.assertEqual(m.solver_config['time_limit_seconds'],90)
        for config in ({'typo':1},{'stages':{'ENERGY':{}}},{'threads':0},{'logging':1},{'relative_gap':-1}):
            with self.assertRaises(ValueError):configuration(config)
        result=m.evaluate(time_index=[])
        self.assertEqual(result['failure']['kind'],'invalid_input')
        record=dict(problem='test',stage='ISP',purpose='market_clearing',status='Not Solved',termination_reason='time_limit')
        with patch.object(m,'run',side_effect=SolveError(record)):
            self.assertEqual(m.evaluate()['failure']['kind'],'solver_limit')

    def test_solver_records_infeasible_and_stage_options(self):
        problem=lp.LpProblem('library_ISP',lp.LpMinimize);x=lp.LpVariable('x',0)
        problem+=x;problem+=x>=1
        with session({'stages':{'ISP':{'time_limit_seconds':3}}}) as context:solve(problem)
        record=context['records'][0]
        self.assertEqual(record['effective_options']['time_limit_seconds'],3)
        self.assertEqual(record['termination_reason'],'optimal')
        problem+=x<=0
        with session() as context:
            with self.assertRaises(SolveError) as failure:solve(problem)
        self.assertEqual(failure.exception.record['termination_reason'],'infeasible')
        self.assertEqual(len(context['records']),1)
        unbounded=lp.LpProblem('unbounded',lp.LpMinimize);y=lp.LpVariable('y',0);unbounded+=-y
        with self.assertRaises(SolveError) as failure:solve(unbounded)
        self.assertEqual(failure.exception.record['termination_reason'],'unbounded')
        cbc=lp.LpProblem('DAM',lp.LpMinimize);z=lp.LpVariable('z',0);cbc+=z;cbc+=z>=2
        with session({'backend':'cbc'}) as context:self.assertAlmostEqual(solve(cbc,False),2)
        self.assertEqual(context['records'][0]['backend'],'cbc')


@unittest.skipUnless(highspy is not None,'Full-day integration requires HiGHS')
class PlanningIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market=market()
        cls.result=cls.market.run(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,
            reserve_requirements={'FCR':{'up':[1]*48,'down':[1]*48}},storage_terminal_soc_mwh={'store':5})

    def test_delivered_terminal_soc_and_cost_reconciliation(self):
        r=self.result
        self.assertTrue(r.audit['passed'],r.audit)
        self.assertAlmostEqual(r.ending_states['store']['soc_mwh'],5,places=5)
        costs=r.economics['assets']
        thermal=sum(row['assets']['unit']['actual_mwh']*60+row['assets']['unit']['start']*7+row['assets']['unit']['on']*3*row['hours'] for row in r.rtbm)
        storage=sum((row['assets']['store']['charge_mwh']+row['assets']['store']['discharge_mwh'])*2 for row in r.rtbm)
        self.assertAlmostEqual(costs['unit']['physical_costs']['totals']['total_operating_cost_eur'],thermal)
        self.assertAlmostEqual(costs['store']['physical_costs']['totals']['total_operating_cost_eur'],storage)
        self.assertTrue(all(v['reconciled'] for v in r.economics['participants'].values()))
        self.assertNotEqual(Decimal(r.economics['participants']['supplier']['unallocated_participant_cash_eur']),0)
        self.assertEqual(r.economics['economic_owners']['portfolio']['participants'],['battery','generator'])
        json.dumps(r.to_evaluation(),allow_nan=False)

    def test_forecast_feasibility_is_not_actual_balance(self):
        r=self.result
        self.assertTrue(r.diagnostics['forecast_clearing_without_slack'])
        self.assertFalse(r.diagnostics['actual_balance']['actual_delivered_balance'])
        self.assertEqual(r.manifest['actual_load_loss_reliability'],'unassessed')
        self.assertTrue(all(v['purpose']!='trajectory_smoothing' or v['stage'] in ('RTBM','RTBM_projection') for v in r.diagnostics['solve_records']))

    def test_ownership_does_not_change_clearing_and_candidate_isolation(self):
        m=market()
        for pid,p in list(m.participants.items()):m.participants[pid]=replace(p,economic_owner='different_owner')
        first=deepcopy(m.assets['store'].initial_state)
        r=m.run(time_index=Market.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,
            reserve_requirements={'FCR':{'up':[1]*48,'down':[1]*48}},storage_terminal_soc_mwh={'store':5})
        self.assertEqual(r.dam,self.result.dam)
        self.assertEqual(r.ending_states,self.result.ending_states)
        self.assertEqual(m.assets['store'].initial_state,first)
        self.assertEqual([r['physical_imbalance_mwh'] for r in r.rtbm],[r['physical_imbalance_mwh'] for r in self.result.rtbm])

    def test_forced_smoothing_failure_falls_back_without_repricing(self):
        import emst.dispatch as dispatch_module
        from emst.calendar import day_intervals
        from datetime import date
        from emst.solver import _purpose
        m=market();assets=[m.assets['unit']]
        intervals=day_intervals(date(2026,11,5),5)[:1]
        inputs=dict(states={'unit':dict(stage='dispatch',on=1,power_mw=50,duration_minutes=480)},
            intervals=intervals,demand=[50.],res=[0.],commercial={'unit':[50/12]},requirements={},rules=m.ruleset.to_dict(),
            commitment={'unit':1},reference={'unit':dict(phase='dispatch')},rtbm=True)
        original=dispatch_module.solve
        def fail_smoothing(problem,mip=True):
            if _purpose.get()=='trajectory_smoothing':raise RuntimeError('forced optional failure')
            return original(problem,mip)
        normal=dispatch_module.dispatch(assets,**inputs)
        with patch.object(dispatch_module,'solve',side_effect=fail_smoothing):fallback=dispatch_module.dispatch(assets,**inputs)
        self.assertEqual(fallback['smoothing']['status'],'fallback_primary')
        self.assertEqual(fallback['rows'][0]['price_eur_mwh'],normal['rows'][0]['price_eur_mwh'])
        self.assertLessEqual(fallback['smoothing']['max_primary_constraint_violation'],1e-5)
        self.assertLessEqual(fallback['smoothing']['primary_objective_deviation_eur'],1e-5)

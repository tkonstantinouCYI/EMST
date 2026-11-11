"""Analytical four-phase commitment, energy and carry-over checks."""
import unittest
from datetime import date,datetime,timezone
from emst import Market,ThermalState
from emst.calendar import day_intervals
from emst.model import PRODUCTS,Unit
from emst.dispatch import dispatch
from emst.isp import schedule
from emst.market_engine import _advance
from emst.optimization import highspy

PROFILE={'sync':[0.], 'soak':[10.,20.], 'desync':[10.,0.]}


class ThermalPhaseTests(unittest.TestCase):
    def market(self,stage='offline',power=0,age=600,profile=None):
        market=Market();market.add_participant('thermal_owner')
        a=market.add_thermal('unit','thermal_owner',capacity_mw=60,minimum_mw=20,
            marginal_cost=10,startup_cost=7,ramp_mw_hour=600,
            phase_profiles_mw=PROFILE if profile is None else profile,
            initial_state=ThermalState(stage,power,age))
        return market,a

    def run_case(self,market,a,demand,requirements=None):
        n=len(demand)
        return dispatch([a],{'unit':dict(stage=a.initial_state.stage,on=int(a.initial_state.stage!='offline'),
            power_mw=a.initial_state.power_mw,duration_minutes=a.initial_state.time_in_stage_minutes)},
            day_intervals(date(2026,11,5),30)[:n],demand,[0]*n,{'unit':[0]*n},
            requirements or {p:[0]*n for p in PRODUCTS},market.ruleset.to_dict())

    def test_complete_cycle_four_binaries_derived_power_and_dispatch_only_reserve(self):
        market,a=self.market();demand=[0,5,15,30,30,15,5,0]
        requirements={p:[1 if p=='FCR_up' and t in (3,4) else 0 for t in range(8)] for p in PRODUCTS}
        result=self.run_case(market,a,demand,requirements)
        rows=[r['assets']['unit'] for r in result['rows']]
        self.assertEqual([r['phase'] for r in rows],['sync','soak','soak','dispatch','dispatch','desync','desync','offline'])
        self.assertEqual(len(result['frozen_discrete']),4*8)
        self.assertEqual(sum(r['start'] for r in rows),1)
        self.assertEqual(rows[0]['start'],1)
        self.assertAlmostEqual(result['fixed_lp_objective_eur'],511)
        for r,d in zip(rows,demand):
            self.assertAlmostEqual(r['power_mw'],d)
            self.assertAlmostEqual((r['up_mwh']-r['down_mwh'])/.5,d)
            self.assertLessEqual(sum(r['phase_binaries'].values()),1)
            if r['phase']!='dispatch':self.assertEqual(sum(r['reserve_mw'].values()),0)
        unit=Unit('unit','thermal_owner',60,20,10,600,0,0,20,startup_eur=7,
            warm_start_multiplier=1,cold_start_multiplier=1,phase_profiles_mw=PROFILE)
        other=schedule([unit],day_intervals(date(2026,11,5),30)[:8],demand,[0]*8,{'unit':[0]*8},
            requirements,{'unit':dict(on=0,end_mw=0,duration_periods=20)},datetime(2026,11,4,tzinfo=timezone.utc),'four_phase_cycle')
        self.assertEqual([r['phase'] for r in other['rows']],[r['phase'] for r in rows])
        self.assertAlmostEqual(other['fixed_lp_objective_eur'],511)

    def test_initial_soak_and_desync_continue_without_a_new_start(self):
        for phase,power,age,demand,expected in (
            ('soak',10,30,[15,30,30],['soak','dispatch','dispatch']),
            ('desync',10,30,[5,0],['desync','offline'])):
            market,a=self.market(phase,power,age)
            result=self.run_case(market,a,demand)
            rows=[r['assets']['unit'] for r in result['rows']]
            self.assertEqual([r['phase'] for r in rows[:2]],expected[:2])
            self.assertEqual(sum(r['start'] for r in rows),0)
            self.assertEqual(result['objective_components_eur']['startup_cost'],0)

    def test_zero_length_start_phases_are_skipped(self):
        market,a=self.market(profile={'sync':[],'soak':[],'desync':[0]})
        result=self.run_case(market,a,[20,20,0])
        rows=[r['assets']['unit'] for r in result['rows']]
        self.assertEqual([r['phase'] for r in rows],['dispatch','desync','offline'])
        self.assertEqual(sum(r['start'] for r in rows),1)

    def test_RTBM_executes_phase_trajectory_and_advances_boundary_state(self):
        market,a=self.market();result=self.run_case(market,a,[0,5,15,30,30,15,5,0])
        state={'unit':dict(stage='offline',on=0,power_mw=0,duration_minutes=600)}
        rtus=day_intervals(date(2026,11,5),5);starts=0
        for t,period in enumerate(result['rows']):
            reference=period['assets']
            for k in range(6):
                intervals=rtus[t*6+k:t*6+6];n=len(intervals)
                load=[reference['unit']['power_mw']]*n
                if reference['unit']['phase'] in ('sync','soak','desync'):
                    start=state['unit']['power_mw'];target=reference['unit']['end_mw']
                    load=[start+(target-start)*(j+.5)/n for j in range(n)]
                r=dispatch([a],state,intervals,load,[0]*n,
                    {'unit':[0]*n},{p:[0]*n for p in PRODUCTS},market.ruleset.to_dict(),
                    commitment={'unit':reference['unit']['on']},reference=reference,rtbm=True)['rows'][0]['assets']['unit']
                starts+=r['start']
                _advance([a],state,{'unit':r},5)
            if reference['unit']['phase'] in ('sync','soak','desync'):
                self.assertAlmostEqual(state['unit']['power_mw'],reference['unit']['end_mw'],places=5)
        self.assertEqual(starts,1)
        self.assertEqual(state['unit']['stage'],'offline')

    def test_one_thermal_per_participant_and_profile_validation(self):
        market,a=self.market()
        with self.assertRaises(ValueError):market.add_thermal('second','thermal_owner',capacity_mw=60,
            minimum_mw=20,marginal_cost=10,initial_state=ThermalState('dispatch',20,60))
        for profile in ({'sync':[0],'soak':[10],'desync':[0]}, {'desync':[1]}, {'invalid':[0]}):
            with self.assertRaises(ValueError):self.market(profile=profile)


@unittest.skipUnless(highspy is not None,'Full phase/handover day requires HiGHS audit extra')
class ThermalPhaseHandoverTests(unittest.TestCase):
    def test_ongoing_soak_at_1100_is_carried_through_forecast_bridge(self):
        import runpy
        from pathlib import Path
        build=runpy.run_path(str(Path(__file__).resolve().parents[1]/'examples/thermal_phases_day.py'),run_name='handover_example')['build_market']
        market,index,demand,history=build(start_period=20)
        result=market.run(time_index=index,system_forecasts={'demand':demand,'res':[0]*288},historical_rtbm_prices=history)
        self.assertTrue(result.audit['passed'],result.audit)
        second=result.isp_runs[1]
        self.assertEqual(second['observed_state']['thermal']['stage'],'soak')
        self.assertEqual(second['observed_state']['thermal']['duration_minutes'],30)
        self.assertAlmostEqual(second['observed_state']['thermal']['power_mw'],10)
        self.assertEqual(second['forecast_noon_state']['thermal']['stage'],'dispatch')
        self.assertEqual(sum(r['assets']['thermal']['start'] for r in result.rtbm),1)
        paid=sum(float(x['cash_eur']) for x in result.ledger if x['component']=='S13' and x['account']=='participant:thermal_owner:nonDAM')
        self.assertAlmostEqual(paid,300)


if __name__=='__main__':unittest.main()

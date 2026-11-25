"""Independent ON/OFF path/event checks and native/hourly daily integration."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date,timedelta
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,unittest
from emst import Market,ThermalState
from emst.calendar import day_intervals,Interval
from emst.dispatch import dispatch
from emst.model import PRODUCTS
from emst.solver import SolveError
from examples.onoff_day import build_market

HISTORY={i:[[80.]*6 for _ in range(3)] for i in range(48)}


def unit(initial=None,up=0,down=0,ramp=720,cost=100,no_load=0):
    m=Market();m.add_participant('owner')
    a=m.add_thermal('g','owner',capacity_mw=220,minimum_mw=75,marginal_cost=50,
        initial_state=initial or ThermalState('offline',0,1440),ramp_mw_hour=ramp,
        minimum_up_minutes=up,minimum_down_minutes=down,startup_cost=cost,
        no_load_cost_per_hour=no_load,commitment_model='on_off',reserve_cost=1)
    return m,a


def clear(m,a,powers,on=None,minutes=30,requests=None,pin_power=True):
    import emst.dispatch as module
    base=day_intervals(date(2026,11,5),30)[0]
    periods=[Interval(f'test:{i}',base.start+timedelta(minutes=i*minutes),minutes,i) for i in range(len(powers))]
    original=module.solve
    def forced(problem,mip=True):
        if mip:
            variables=problem.variablesDict()
            for j,p in enumerate(powers):
                if on is not None:problem+=variables[f'thermal_0_dispatch_{j}']==on[j]
                if pin_power:problem+=(p*minutes/60+variables[f'up_0_{j}']-variables[f'down_0_{j}'])/(minutes/60)==p
        return original(problem,mip)
    s=a.initial_state
    with patch.object(module,'solve',side_effect=forced):
        return dispatch([a],{'g':dict(stage=s.stage,on=int(s.stage=='dispatch'),power_mw=s.power_mw,duration_minutes=s.time_in_stage_minutes)},
            periods,powers,[0.]*len(powers),{'g':[p*minutes/60 for p in powers]},
            {p:[(requests or {}).get(p,0.)]*len(powers) for p in PRODUCTS},m.ruleset.to_dict())


def flat_market(power=100,ramp=10):
    m=Market();m.add_participant('owner');m.add_participant('backup_owner');m.add_participant('supplier')
    m.add_thermal('g','owner',capacity_mw=220,minimum_mw=75,marginal_cost=50,
        initial_state=ThermalState('dispatch',power,1440),ramp_mw_hour=ramp,startup_cost=1000000,
        minimum_up_minutes=120,minimum_down_minutes=60,commitment_model='on_off')
    m.add_thermal('backup','backup_owner',capacity_mw=220,minimum_mw=0,marginal_cost=200,
        initial_state=ThermalState('dispatch',0,1440),ramp_mw_hour=10000,
        minimum_up_minutes=0,minimum_down_minutes=0,commitment_model='on_off')
    m.add_supplier('load','supplier',demand_forecast=[power]*48,actual_consumption=[power]*48)
    return m


class OnOffFormulationTests(unittest.TestCase):
    def test_explicit_modes_and_policy_validation(self):
        m=Market();m.add_participant('o')
        args=dict(capacity_mw=220,minimum_mw=75,marginal_cost=50,initial_state=ThermalState('dispatch',100,1440))
        for invalid in ({'commitment_model':'bad'},{'commitment_model':'on_off','boundary_reserve_policy':'normal'},
            {'ramp_up_mw_hour':5},{'commitment_model':'on_off','ramp_down_mw_hour':-1}):
            with self.assertRaises(ValueError):m.add_thermal('g','o',**args,**invalid)
        a=m.add_thermal('g','o',**args)
        self.assertNotIn('commitment_model',a.parameters)

    def test_directional_reserve_response_limits(self):
        m,a=unit(ThermalState('dispatch',100,1440));a.parameters['ramp_up_mw_hour']=10
        r=clear(m,a,[100],[1],requests={'FCR_up':1,'FCR_down':10})
        self.assertAlmostEqual(r['rows'][0]['assets']['g']['reserve_mw']['FCR_up'],10/120)
        self.assertAlmostEqual(r['rows'][0]['assets']['g']['reserve_mw']['FCR_down'],6)
    def test_first_last_one_interval_and_jump_exemption(self):
        for minutes in (30,60):
            for powers,on in (([0,75,0],[0,1,0]),([0,75,75,0],[0,1,1,0])):
                m,a=unit(ramp=1,cost=0)
                r=clear(m,a,powers,on,minutes,requests={'FCR_up':1,'FCR_down':1})
                self.assertEqual(r['thermal_state_binary_count'],len(on))
                self.assertEqual(len(r['frozen_discrete']),len(on))
                self.assertEqual([x['assets']['g']['start'] for x in r['rows']],[int(v and not (on[j-1] if j else 0)) for j,v in enumerate(on)])
                self.assertTrue(all(not any(x['assets']['g']['reserve_mw'].values()) for x in r['rows']))
                self.assertTrue(all(x['assets']['g']['phase_binaries']['sync']==0 for x in r['rows']))

    def test_interior_dispatch_reserves_and_once_only_costs(self):
        m,a=unit(up=60,cost=123,no_load=7)
        r=clear(m,a,[0,75,150,75,0],[0,1,1,1,0],requests={'FCR_up':3,'FCR_down':3})
        self.assertAlmostEqual(r['rows'][2]['assets']['g']['reserve_mw']['FCR_up'],3)
        self.assertAlmostEqual(r['objective_components_eur']['startup_cost'],123)
        self.assertAlmostEqual(r['objective_components_eur']['no_load_cost'],7*1.5)
        self.assertAlmostEqual(r['objective_components_eur']['total'],r['fixed_lp_objective_eur'])

    def test_boundary_power_violation_and_consecutive_on_ramps(self):
        for powers in ([0,100,0],[0,75,100,0]):
            m,a=unit()
            with self.assertRaises(SolveError):clear(m,a,powers,[int(p>0) for p in powers])
        m,a=unit(ramp=10)
        with self.assertRaises(SolveError):clear(m,a,[0,75,150],[0,1,1])

    def test_exact_events_against_enumerated_onoff_reference(self):
        # Independent duration replay, including obligations extending past horizon.
        for initial_on in (False,True):
            for bits in product((0,1),repeat=4):
                age=30;prior=int(initial_on);allowed=True;starts=0
                for b in bits:
                    if b!=prior:
                        allowed &= age>=60;starts+=int(b==1);age=0
                    age+=30;prior=b
                m,a=unit(ThermalState('dispatch' if initial_on else 'offline',75 if initial_on else 0,30),up=60,down=60,cost=0)
                if not allowed:
                    with self.assertRaises(SolveError):clear(m,a,[75*b for b in bits],bits)
                else:
                    r=clear(m,a,[75*b for b in bits],bits)
                    self.assertEqual(sum(x['assets']['g']['start'] for x in r['rows']),starts)

    def test_initial_and_terminal_boundaries_and_directional_ramps(self):
        m,a=unit(ThermalState('dispatch',150,1440),ramp=10)
        r=clear(m,a,[150],[1])
        self.assertEqual(r['rows'][0]['assets']['g']['start'],0)
        self.assertFalse(r['rows'][0]['assets']['g']['boundary_minimum'])
        with self.assertRaises(SolveError):clear(m,a,[0],[0])
        m,a=unit(ThermalState('dispatch',75,1440))
        self.assertEqual(clear(m,a,[0],[0])['rows'][0]['assets']['g']['stop'],1)
        m,a=unit(ThermalState('dispatch',75,1440));a.parameters['ramp_up_mw_hour']=10
        with self.assertRaises(SolveError):clear(m,a,[85],[1])

    def test_trailing_minimum_duration_obligation_survives_new_horizon(self):
        m,a=unit(up=150,cost=123)
        first=clear(m,a,[75],[1],minutes=60)
        self.assertEqual(first['rows'][0]['assets']['g']['start'],1)
        m,a=unit(ThermalState('dispatch',75,60),up=150,cost=123)
        with self.assertRaises(SolveError):clear(m,a,[75,0],[1,0],minutes=60)
        second=clear(m,a,[75,75,0],[1,1,0],minutes=60)
        self.assertEqual(second['objective_components_eur']['startup_cost'],0)

    def test_non_aligned_minimum_duration_uses_ceiling(self):
        m,a=unit(up=90)
        with self.assertRaises(SolveError):clear(m,a,[75,0],[1,0],minutes=60)
        self.assertEqual(clear(m,a,[75,75,0],[1,1,0],minutes=60)['thermal_state_binary_count'],3)


class OnOffDailyTests(unittest.TestCase):
    def test_unsupported_grids_are_rejected_explicitly(self):
        from emst import StorageState
        m=flat_market()
        with self.assertRaisesRegex(NotImplementedError,'30-minute sequential RTBM'):
            m.run(time_index=m.time_index('2026-11-05'),isp_interval_minutes=60)
        with self.assertRaisesRegex(ValueError,'isp_interval_minutes'):
            m.run(time_index=m.time_index('2026-11-05'),isp_interval_minutes=True,rtbm_interval_minutes=30)
        m.add_participant('storage_owner');m.add_storage('battery','storage_owner',power_mw=2,capacity_mwh=4,initial_state=StorageState(0,2))
        with self.assertRaisesRegex(NotImplementedError,'storage retains'):
            m.run(time_index=m.time_index('2026-11-05'),isp_interval_minutes=60,rtbm_interval_minutes=30)

    def test_noon_shutdown_cannot_cancel_preceding_reserve_awards(self):
        import emst.market_engine as engine
        import emst.dispatch as module
        m=flat_market(75,720)
        m.assets['g'].parameters['reserve_prices']={p:.1 for p in PRODUCTS}
        m.assets['backup'].parameters['reserve_prices']={p:100 for p in PRODUCTS}
        original=engine.dispatch
        def prescribed(*args,**kw):
            if kw.get('rtbm'):return original(*args,**kw)
            intervals=args[2];solve=module.solve
            def pin(problem,mip=True):
                if mip:
                    v=problem.variablesDict()
                    for j in range(len(intervals)):
                        problem+=v[f'thermal_0_dispatch_{j}']==int(intervals[0].period==0)
                        problem+=v[f'thermal_1_dispatch_{j}']==1
                return solve(problem,mip)
            with patch.object(module,'solve',side_effect=pin):return original(*args,**kw)
        with patch.object(engine,'dispatch',side_effect=prescribed),self.assertRaises(SolveError):
            m.run(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,isp_interval_minutes=60,
                rtbm_interval_minutes=30,reserve_requirements={'FCR':{'up':[1.]*48,'down':[0.]*48}})

    def test_start_across_noon_and_boundary_delivery_costs_once(self):
        import emst.market_engine as engine
        import emst.dispatch as module
        for minutes in (30,60):
            m=flat_market(75,720)
            m.assets['g'].initial_state=ThermalState('offline',0,1440)
            m.assets['g'].parameters['startup_cost']=123
            original=engine.dispatch
            def prescribed(*args,**kw):
                if kw.get('rtbm'):return original(*args,**kw)
                intervals=args[2];solve=module.solve
                def pin(problem,mip=True):
                    if mip:
                        v=problem.variablesDict()
                        for j,t in enumerate(intervals):
                            problem+=v[f'thermal_0_dispatch_{j}']==int(22<=t.period<28)
                            problem+=v[f'thermal_1_dispatch_{j}']==1
                    return solve(problem,mip)
                with patch.object(module,'solve',side_effect=pin):return original(*args,**kw)
            with patch.object(engine,'dispatch',side_effect=prescribed):
                r=m.run(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,
                    isp_interval_minutes=minutes,rtbm_interval_minutes=30,reserve_requirements={'FCR':{'up':[1.]*48,'down':[1.]*48}})
            self.assertTrue(r.audit['passed'],r.audit)
            self.assertEqual(sum(row['assets']['g']['start'] for row in r.rtbm),1)
            self.assertEqual(r.isp_runs[1]['initial_state']['g']['duration_minutes'],60)
            self.assertEqual(r.isp_runs[1]['objective_components_eur']['startup_cost'],0)
            self.assertEqual(r.economics['assets']['g']['physical_costs']['totals']['startup_cost_eur'],123)
            self.assertTrue(all(row['assets']['g']['power_mw']==75 for row in r.rtbm[22:22+minutes//30]))
            self.assertTrue(all(row['assets']['g']['power_mw']==75 for row in r.rtbm[28-minutes//30:28]))
    def test_native_hourly_stages_reporting_and_binary_counts(self):
        for minutes in (30,60):
            m,q,h=build_market()
            r=m.run(time_index=m.time_index('2026-11-05'),reserve_requirements=q,historical_rtbm_prices=h,
                isp_interval_minutes=minutes,rtbm_interval_minutes=30)
            self.assertTrue(r.audit['passed'],r.audit)
            self.assertEqual([len(x['rows']) for x in r.isp_runs],[1440//minutes,720//minutes])
            self.assertEqual([x['thermal_state_binary_count'] for x in r.isp_runs],[2*1440//minutes,2*720//minutes])
            self.assertEqual(len(r.meters),48*len(m.assets))
            self.assertTrue(all(x['reconciled'] for x in r.economics['participants'].values()))
            self.assertNotIn('plant_configurations',m.assets['ccgt'].parameters)
            self.assertNotIn('transition_cost_eur',r.economics['assets']['ccgt']['physical_costs']['totals'])
            readable=r.to_readable(audience='participant',participant='cc_owner')
            records=readable['simulation_summary']['days'][0]['market_stages']['isp']['runs'][0]['participants']['cc_owner']['intervals']
            self.assertTrue(all(x['commitment_model']=='on_off' for x in records))
            self.assertAlmostEqual(records[0]['market_schedule_mwh'],sum(v['MSQ_mwh'] for v in r.meters if v['asset']=='ccgt' and v['period']<minutes//30))
            summary=r.to_readable()['simulation_summary']['days'][0]['market_stages']['isp']['runs'][0]['summary']
            self.assertEqual(summary['isp_interval_minutes'],minutes)
            self.assertEqual(summary['thermal_models'],['on_off'])

    def test_hourly_reserve_prices_are_allocated_once_to_half_hours(self):
        from emst.isp_resolution import accounting
        periods=day_intervals(date(2026,11,5),30)
        row=dict(period=0,at=periods[0].start.isoformat(),hours=1,reserve_prices={'FCR_up':12},
            assets={'g':dict(power_mw=100,energy_mwh=100,up_mwh=0,down_mwh=0,reserve_mw={'FCR_up':3})})
        mapped=accounting({'rows':[row]},periods,{'g':[20,80]})
        self.assertEqual([mapped[i]['assets']['g']['energy_mwh'] for i in (0,1)],[50,50])
        self.assertEqual([mapped[i]['assets']['g']['up_mwh']-mapped[i]['assets']['g']['down_mwh'] for i in (0,1)],[30,-30])
        self.assertEqual(sum(mapped[i]['reserve_prices']['FCR_up']*3 for i in (0,1)),36)

    def test_noon_shutdown_preserves_history_and_full_last_hour(self):
        import emst.market_engine as engine
        import emst.dispatch as module
        for minutes in (30,60):
            m=flat_market(75,720);original=engine.dispatch
            def prescribed(*args,**kw):
                if kw.get('rtbm'):return original(*args,**kw)
                intervals=args[2];solve=module.solve
                def pin(problem,mip=True):
                    if mip:
                        v=problem.variablesDict()
                        for j in range(len(intervals)):
                            problem+=v[f'thermal_0_dispatch_{j}']==int(intervals[0].period==0)
                            problem+=v[f'thermal_1_dispatch_{j}']==1
                    return solve(problem,mip)
                with patch.object(module,'solve',side_effect=pin):return original(*args,**kw)
            with patch.object(engine,'dispatch',side_effect=prescribed):
                r=m.run(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,isp_interval_minutes=minutes,rtbm_interval_minutes=30)
            self.assertTrue(r.audit['passed'],r.audit)
            last=r.rtbm[24-minutes//30:24]
            self.assertTrue(all(abs(x['assets']['g']['power_mw']-75)<1e-6 for x in last))
            self.assertEqual(r.rtbm[24]['assets']['g']['on'],0)
            self.assertEqual(r.rtbm[24]['assets']['g']['stop'],1)
            self.assertEqual(r.isp_runs[1]['initial_state']['g']['duration_minutes'],2160)

    def test_slow_shutdown_midnight_and_checkpoint_resume(self):
        from tests_support import without_runtime
        for minutes in (30,60):
            m=flat_market(100,10)
            scenarios={day:dict(forecasts={'load':[load]*48},actuals={'load':[load]*48},
                historical_rtbm_prices=HISTORY,isp_interval_minutes=minutes,rtbm_interval_minutes=30)
                for day,load in (('2026-11-05',100),('2026-11-06',20))}
            with TemporaryDirectory() as folder:
                root=Path(folder)
                full=m.run_period(start='2026-11-05',end='2026-11-06',scenario=scenarios,checkpoint_directory=root/'full')
                first=m.run_period(start='2026-11-05',end='2026-11-05',scenario=scenarios,checkpoint_directory=root/'split')
                altered=deepcopy(scenarios);altered['2026-11-06']['isp_interval_minutes']=90-minutes
                with self.assertRaisesRegex(ValueError,'ISP resolution'):
                    m.run_period(start='2026-11-06',end='2026-11-06',scenario=altered,checkpoint_directory=root/'split',resume_from=first.checkpoint)
                resumed=m.run_period(start='2026-11-06',end='2026-11-06',scenario=scenarios,
                    checkpoint_directory=root/'split',resume_from=first.checkpoint)
                for day in full.days:
                    self.assertTrue(full.days[day].audit['passed'],full.days[day].audit)
                    self.assertEqual(without_runtime(json.loads(json.dumps(asdict(full.days[day])))),
                        without_runtime(json.loads(json.dumps(asdict(resumed.days[day])))))
                end=first.days['2026-11-05'].ending_states['g']
                self.assertEqual(end['power_mw'],100)
                self.assertEqual(end['time_in_stage_minutes'],2880)
                rows=full.days['2026-11-06'].isp_runs[0]['rows']
                off=next(j for j,row in enumerate(rows) if not row['assets']['g']['on'])
                self.assertGreaterEqual(off*minutes,150)
                self.assertEqual(rows[off-1]['assets']['g']['power_mw'],75)
                self.assertFalse(any(x['assets']['g']['start'] for x in full.days['2026-11-06'].rtbm))

    def test_hourly_balance_and_capacity_cash_with_varying_half_hours(self):
        m=flat_market(100,10000);load=[90.,110.]*24
        m.assets['load'].forecast=load;m.assets['load'].actual=load
        r=m.run(time_index=m.time_index('2026-11-05'),historical_rtbm_prices=HISTORY,
            reserve_requirements={'FCR':{'up':[1.,3.]*24,'down':[2.]*48}},isp_interval_minutes=60,rtbm_interval_minutes=30)
        self.assertTrue(r.audit['passed'],r.audit)
        self.assertEqual(r.isp_runs[0]['demand_mw'],[100.]*24)
        self.assertEqual(r.isp_runs[0]['requirements_mw']['FCR_up'],[3.]*24)
        expected=sum(row['reserve_prices'][p]*sum(a['reserve_mw'][p] for a in row['assets'].values())
            for run in r.isp_runs for row in run['rows'] if run['id']=='ISP_1100' or row['period']<24 for p in ('FCR_up','FCR_down'))
        actual=sum(float(e['cash_eur']) for e in r.ledger if e['component']=='S05' and e['account'].startswith('participant:'))
        self.assertAlmostEqual(expected,actual,places=5)


if __name__=='__main__':unittest.main()

"""Independent state enumeration and bidirectional physical-solution lifting."""
from copy import deepcopy
from itertools import product
from pathlib import Path
from datetime import date
import json,unittest
import pulp as lp
from emst.configurations import states,boundary,key_at_boundary,advance,PlantState
from emst.configurations_reference import occupancy as reference
from emst.configurations_compact import occupancy as compact
from emst.calendar import day_intervals
from emst.optimization import solve,primal_violation,highspy
from emst.dispatch import dispatch
from emst.model import PRODUCTS
from examples.configured_plant import build_market
from tools.benchmark_configurations import capture
from examples.synthetic_configured_fleet import synthetic_case


def solve_occupancy(function,data,initial,sequence):
    specs=states(data);names=list(specs);m=lp.LpProblem('enumerated',lp.LpMinimize)
    z,entries,counts=function(m,data,specs,names,len(sequence),'enum',initial,key_at_boundary(initial))
    for t,s in enumerate(sequence):m+=z[s,t]==1
    m+=lp.lpSum(entries[e['id'],t]*e['cost_eur'] for e in data['transitions'] for t in range(len(sequence)))
    status=m.solve(lp.HiGHS(msg=False,threads=1,mip=True))
    return status,lp.value(m.objective),m,z,counts


def oracle(data,initial,sequence):
    state=deepcopy(initial);specs=states(data);cost=0
    for key in sequence:
        spec=specs[key]
        if state.get('transition'):
            if key!=key_at_boundary(state):return None
        elif key!=state['configuration']:
            if 'edge' not in spec or spec['step']!=0 or spec['edge']['source']!=state['configuration']:return None
            cost+=spec['edge']['cost_eur']
        for c,h in state['component_histories'].items():
            if h['on']!=(c in spec['components']) and h['duration_minutes']<data['components'][c]['minimum_up_minutes' if h['on'] else 'minimum_down_minutes']:return None
        # Only the automaton/history is enumerated; physical power is tested below.
        fake=type('Asset',(),{'parameters':{'plant_configurations':data}})()
        state=advance(fake,state,dict(configuration_state=key,end_mw=spec.get('endpoint',spec['minimum'])),30)
    return cost


@unittest.skipUnless(highspy is not None,'Equivalence enumeration requires the optional HiGHS audit extra')
class CompactEquivalenceTests(unittest.TestCase):
    def test_empty_transition_is_a_stable_boundary(self):
        a=build_market().assets['ccgt'];data=a.parameters['plant_configurations']
        initial=boundary(a.initial_state);initial['transition']={}
        for function in (reference,compact):
            status,value,*_=solve_occupancy(function,data,initial,['GT1_ST']*3)
            self.assertEqual(status,lp.LpStatusOptimal)
            self.assertAlmostEqual(value,0)

    def test_enumerated_integer_paths_match_independent_oracle(self):
        data=dict(components={'GT':dict(minimum_up_minutes=60,minimum_down_minutes=60)},
            configurations={'OFF':dict(components=[],minimum_mw=0,maximum_mw=0,marginal_cost=0,no_load_cost_per_hour=0,reserve_limit_mw=0,ramp_mw_hour=100),
                'ON':dict(components=['GT'],minimum_mw=10,maximum_mw=50,marginal_cost=30,no_load_cost_per_hour=0,reserve_limit_mw=10,ramp_mw_hour=100)},
            transitions=[dict(id='start',source='OFF',target='ON',profile_mw=[10,20],components_by_step=[['GT'],['GT']],cost_eur=11,ramp_mw_hour=100),
                dict(id='stop',source='ON',target='OFF',profile_mw=[10,0],components_by_step=[['GT'],[]],cost_eur=3,ramp_mw_hour=100)])
        initials=[boundary(PlantState('OFF',0,{'GT':dict(on=False,duration_minutes=90)})),
            boundary(PlantState('ON',20,{'GT':dict(on=True,duration_minutes=0)})),
            boundary(PlantState('OFF',10,{'GT':dict(on=True,duration_minutes=30)},dict(id='start',elapsed_minutes=30)))]
        names=list(states(data));checked=0
        for initial in initials:
            for sequence in product(names,repeat=3):
                expected=oracle(data,initial,sequence)
                for function in (reference,compact):
                    status,value,m,z,counts=solve_occupancy(function,data,initial,sequence)
                    self.assertEqual(status==lp.LpStatusOptimal,expected is not None,(function.__module__,initial,sequence))
                    if expected is not None:
                        self.assertAlmostEqual(value,expected)
                        for v in m.variables():
                            if '_component_start_' in v.name or '_component_stop_' in v.name:self.assertAlmostEqual(v.value(),round(v.value()))
                checked+=1
        self.assertEqual(checked,648)

    def test_CC4_binary_count_with_13_step_transitions(self):
        from examples.synthetic_configured_fleet import plant_spec,build_market
        data=synthetic_case('fleet')
        for variant,expected in (('reference',66*48),('compact',12*48)):
            m=build_market(data,variant);a=m.assets['ccgt_0'];model=lp.LpProblem('counts')
            specs=states(a.parameters['plant_configurations']);initial=boundary(a.initial_state)
            function=reference if variant=='reference' else compact
            z,x,counts=function(model,a.parameters['plant_configurations'],specs,list(specs),48,'test',initial,key_at_boundary(initial))
            self.assertEqual(sum(v.isBinary() for v in model.variables()),expected)
            if variant=='compact':self.assertEqual(counts['component_event_binary_count'],0)
        self.assertEqual(max(len(e['profile_mw']) for e in a.parameters['plant_configurations']['transitions']),13)

    def test_bidirectional_lifting_preserves_actual_power_reserves_and_cost(self):
        from unittest.mock import patch
        import emst.dispatch as module
        for inherited in (False,True):
            saved={}
            for variant in ('reference','compact'):
                m=build_market();a=m.assets['ccgt'];a.parameters['configuration_formulation']=variant
                if inherited:
                    a.initial_state=PlantState('GT1_ST',107,{c:dict(on=True,duration_minutes=30 if c=='GT2' else 1000) for c in ('GT1','GT2','ST')},dict(id='add2',elapsed_minutes=30))
                initial=boundary(a.initial_state);original=module.solve;capture_box={}
                def take(problem,mip=True):
                    pending=deepcopy(problem) if mip else None
                    result=original(problem,mip)
                    if mip:
                        values={v.name:v.value() for v in problem.variables()}
                        for v in pending.variables():v.varValue=values[v.name]
                        capture_box['model']=pending;capture_box['objective']=result
                    return result
                with patch.object(module,'solve',side_effect=take):
                    demand=[113.5,130,130,100] if inherited else [80,110,130,100]
                    r=dispatch([a],{'ccgt':initial},day_intervals(date(2026,11,5),30)[:4],demand,[0]*4,
                        {'ccgt':[v*.5 for v in demand]},{p:[0.]*4 for p in PRODUCTS},m.ruleset.to_dict())
                saved[variant]=(capture_box['model'],capture_box['objective'])
            self.assertAlmostEqual(saved['reference'][1],saved['compact'][1],places=5)
            for source,target in (('reference','compact'),('compact','reference')):
                src,obj=saved[source];dst=deepcopy(saved[target][0]);values={v.name:v.value() for v in src.variables()}
                for v in dst.variables():
                    if v.name in values:dst+=v==values[v.name]
                    v.cat=lp.LpContinuous
                for aid,z in dst._emst_configuration_states.items():
                    for key,expr in z.items():dst+=expr==lp.value(src._emst_configuration_states[aid][key])
                self.assertAlmostEqual(solve(dst,False),obj,places=4)
                self.assertLess(primal_violation(dst.variables(),list(dst.constraints.values())),1e-5)

    def test_13_step_transition_horizon_edge_and_RTBM_carryover(self):
        from unittest.mock import patch
        import emst.dispatch as module
        outcomes={}
        for variant in ('reference','compact'):
            m=build_market();a=m.assets['ccgt'];a.parameters['configuration_formulation']=variant
            edge=next(e for e in a.parameters['plant_configurations']['transitions'] if e['id']=='add2')
            edge['profile_mw']=[100+40*k/12 for k in range(13)]
            edge['components_by_step']=[['GT1','GT2','ST'] for _ in range(13)]
            original=module.solve
            def pin(problem,mip=True):
                if mip:
                    problem+=problem._emst_configuration_states['ccgt']['GT1_ST',0]==1
                    problem+=problem._emst_configuration_states['ccgt']['add2:0',1]==1
                return original(problem,mip)
            initial=boundary(a.initial_state)
            with patch.object(module,'solve',side_effect=pin):
                first=dispatch([a],{'ccgt':initial},day_intervals(date(2026,11,5),30)[-2:],[60,80],[0,0],{'ccgt':[30,40]},
                    {p:[0,0] for p in PRODUCTS},m.ruleset.to_dict())
            state=initial
            for r in first['rows']:
                row=r['assets']['ccgt'];state=advance(a,state,dict(row,end_mw=row['configuration_endpoint_mw']),30)
            self.assertEqual(state['transition'],{'id':'add2','elapsed_minutes':30})
            self.assertEqual(sum(r['assets']['ccgt']['transition_cost_eur'] for r in first['rows']),250)
            averages=[(edge['profile_mw'][k-1]+edge['profile_mw'][k])/2 for k in range(1,13)]
            plan=dispatch([a],{'ccgt':state},day_intervals(date(2026,11,6),30)[:12],averages,[0]*12,{'ccgt':[v*.5 for v in averages]},
                {p:[0]*12 for p in PRODUCTS},m.ruleset.to_dict())
            self.assertEqual([r['assets']['ccgt']['configuration_state'] for r in plan['rows']],[f'add2:{k}' for k in range(1,13)])
            for minutes in (5,30):
                current=deepcopy(state);cost=0
                for i,t in enumerate(day_intervals(date(2026,11,6),minutes)[:12*30//minutes]):
                    spec=states(a.parameters['plant_configurations'])[key_at_boundary(current)]
                    remaining=30-current['transition']['elapsed_minutes']%30
                    expected_end=current['power_mw']+(spec['endpoint']-current['power_mw'])*minutes/remaining
                    average=(current['power_mw']+expected_end)/2
                    ref=plan['rows'][i//(30//minutes)]['assets']['ccgt']
                    result=dispatch([a],{'ccgt':current},[t],[average],[0],{'ccgt':[averages[i//(30//minutes)]*t.hours]},
                        {},m.ruleset.to_dict(),rtbm=True,reference={'ccgt':ref},immediate_ramping=minutes==30)
                    row=result['rows'][0]['assets']['ccgt']
                    self.assertAlmostEqual(row['end_mw'],expected_end,places=6)
                    self.assertAlmostEqual(row['energy_mwh'],average*t.hours,places=6)
                    self.assertEqual(row['reserve_mw'],{})
                    cost+=row['transition_cost_eur'];current=advance(a,current,row,minutes)
                self.assertEqual(cost,0)
                self.assertIsNone(current['transition']);self.assertEqual(current['configuration'],'GT1_GT2_ST')
                self.assertEqual(current['component_histories']['GT2']['duration_minutes'],390)
                outcomes[variant,minutes]=current
        for v in outcomes.values():self.assertEqual(v,outcomes['reference',5])

    def test_initial_relaxation_is_not_weaker_on_synthetic_case(self):
        bounds={}
        for variant in ('reference','compact'):
            model=capture(synthetic_case('small'),variant)['problem']
            bounds[variant]=solve(model,False)
        self.assertGreaterEqual(bounds['compact']+1e-5,bounds['reference'])

if __name__=='__main__':unittest.main()

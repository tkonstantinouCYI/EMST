"""Optional primal ISP hints, full-constraint repair, and cold-start fallback.

Repair never fixes decisions or changes constraints in the market-clearing MILP.
Its auxiliary objective is only a candidate generator, never a market price.
"""
from copy import deepcopy
import time
import pulp as lp
from . import solver as control
from . import configurations as config
from .phases import PHASES,boundary_state


def validate_seed(seed):
    if not isinstance(seed,dict) or not isinstance(seed.get('rows'),list):
        raise ValueError('isp_warm_start must be an ISP run containing rows')
    seen=set()
    for row in seed['rows']:
        if not isinstance(row,dict) or type(row.get('period')) is not int or not 0<=row['period']<48 or row['period'] in seen or not isinstance(row.get('assets'),dict):
            raise ValueError('Warm-start rows need unique MTU periods and asset mappings')
        seen.add(row['period'])
        for value in row['assets'].values():
            if not isinstance(value,dict):raise ValueError('Invalid warm-start asset row')
            for key in ('configuration_state','phase'):
                if key in value and not isinstance(value[key],str):raise ValueError('Warm-start state labels must be strings')


def hints(problem,assets,states,intervals,commercial,seed):
    if seed is not None:validate_seed(seed)
    prior={r['period']:r['assets'] for r in seed['rows']} if seed is not None else {}
    variables={v.name:v for v in problem.variables()}
    result={name:0 for name,v in variables.items() if v.isInteger()}
    def put(name,value):
        if name in result:result[name]=int(value)
    thermal=[a for a in assets if a.technology=='thermal']
    for i,a in enumerate(thermal):
        initial=states[a.id]
        if config.configured(a):
            data=a.parameters['plant_configurations'];specs=config.states(data);state=deepcopy(initial)
            old_components={c:h['on'] for c,h in initial['component_histories'].items()}
            for j,t in enumerate(intervals):
                given=prior.get(t.period,{}).get(a.id,{})
                chosen=given.get('configuration_state',config.key_at_boundary(state))
                if chosen not in specs:chosen=config.key_at_boundary(state)
                for (s,k),expr in problem._emst_configuration_states[a.id].items():
                    if k==j and isinstance(expr,lp.LpVariable):put(expr.name,s==chosen)
                for ei,e in enumerate(data['transitions']):
                    entered=chosen==e['id']+':0' and not (j==0 and initial.get('transition'))
                    put(f'plant_{a.id}_transition_start_{ei}_{j}',entered)
                for ci,c in enumerate(data['components']):
                    on=c in specs[chosen]['components']
                    put(f'plant_{a.id}_component_start_{ci}_{j}',on and not old_components[c])
                    put(f'plant_{a.id}_component_stop_{ci}_{j}',old_components[c] and not on)
                    old_components[c]=on
                # This is only a preferred path. The repair MILP decides legality
                # against the actual initial boundary, including component clocks.
                state=config.advance(a,state,dict(configuration_state=chosen,end_mw=specs[chosen].get('endpoint',initial['power_mw'])),30)
        else:
            for j,t in enumerate(intervals):
                phase,_=boundary_state(initial['stage'],initial['duration_minutes']+30*j,a.parameters['phase_profiles_mw'])
                phase=prior.get(t.period,{}).get(a.id,{}).get('phase',phase)
                for q in PHASES:put(f'thermal_{i}_{q}_{j}',q==phase)
    for i,a in enumerate(a for a in assets if a.technology=='storage'):
        for j,t in enumerate(intervals):
            row=prior.get(t.period,{}).get(a.id,{})
            mode=row.get('physical_charging_mode',int(commercial[a.id][j]<0))
            if mode not in (0,1):mode=0
            up=row.get('activation_up_mode',1-mode)
            put(f'storage_mode_{i}_{j}',mode)
            put(f'storage_activation_mode_{i}_{j}',up if up in (0,1) else 0)
    return result


def prepare(problem,assets,states,intervals,commercial,seed=None):
    options=control.options('ISP')
    if not options['warm_start']:return
    from .optimization import highspy,primal_violation
    started=time.perf_counter()
    report=dict(enabled=True,source='provided_ISP_schedule' if seed is not None else 'boundary_continuation',
        requested_source=options['warm_start_source'],
        repair_time_limit_seconds=options['warm_start_repair_seconds'],candidate_available=False,
        submission_status='not_submitted',final_problem_unrestricted=True,
        final_relative_gap=options['relative_gap'],final_absolute_gap=options['absolute_gap'])
    problem._emst_warm_start=report
    preferred=hints(problem,assets,states,intervals,commercial,seed)
    report['hinted_binary_count']=len(preferred)
    repaired=deepcopy(problem);repaired.name='ISP_warm_start_repair'
    variables=list(repaired.variables())  # PuLP may append a dummy column during solve.
    # Name-based objective lookup is required: variables belong to the copy.
    costs={v.name:c for v,c in problem.objective.items()}
    security=lp.lpSum(costs.get(v.name,0)*v for v in variables if v.name.startswith(('energy_shortage_mwh_','energy_surplus_mwh_','reserve_shortage_mw_')))
    economic=repaired.objective
    backend=options['backend'];backend='highs' if backend=='auto' and highspy is not None else 'cbc' if backend=='auto' else backend
    cls=lp.HiGHS if backend=='highs' else lp.PULP_CBC_CMD
    solve_started=time.perf_counter()
    original={v.name:(v.lowBound,v.upBound,v.cat) for v in variables}
    report['repair_attempts']=[]
    lp_seconds=0.
    try:
        if options['warm_start_source']=='lp':
            lp_started=time.perf_counter()
            for v in variables:v.cat=lp.LpContinuous
            status=repaired.solve(cls(msg=options['logging'],mip=False,threads=options['threads'],timeLimit=options['warm_start_repair_seconds'],gapRel=0.,gapAbs=0.))
            lp_seconds=time.perf_counter()-lp_started
            report['LP_relaxation_status']=lp.LpStatus.get(status,'Undefined')
            if status==lp.LpStatusOptimal and repaired.sol_status==lp.LpSolutionOptimal and primal_violation(variables,list(repaired.constraints.values()))<=1e-5:
                # For binary v, |v-p| = p+(1-2p)*v. This retains the
                # relaxed values' confidence rather than treating 0.51 as 1.0.
                preferred={v.name:max(0.,min(1.,float(v.varValue))) for v in variables if v.name in preferred}
                report.update(source='LP_relaxation',LP_relaxation_objective_eur=float(lp.value(economic)),
                    LP_fractional_binary_count=sum(abs(p-round(p))>1e-6 for p in preferred.values()))
            else:report['LP_guidance_fallback']='schedule_hints'
            for v in variables:v.lowBound,v.upBound,v.cat=original[v.name]
        rounded={name:int(p>=.5) for name,p in preferred.items()}
        distance=lp.lpSum(p+(1-2*p)*v for v in variables if (p:=preferred.get(v.name)) is not None)
        repair_objective=distance+security+1e-6*economic
        for kind in ('fixed_hint_LP','free_commitment_MILP'):
            remaining=options['warm_start_repair_seconds']-(time.perf_counter()-solve_started)
            if remaining<=0:break
            for v in variables:
                v.lowBound,v.upBound,v.cat=original[v.name]
                if kind=='fixed_hint_LP' and v.name in preferred:
                    v.lowBound=v.upBound=rounded[v.name];v.cat=lp.LpContinuous
                v.varValue=None
            repaired.setObjective(economic if kind=='fixed_hint_LP' else repair_objective)
            status=repaired.solve(cls(msg=options['logging'],mip=kind!='fixed_hint_LP',threads=options['threads'],timeLimit=remaining,gapRel=0.,gapAbs=0.))
            report['repair_status']=lp.LpStatus.get(status,'Undefined')
            report['repair_solution_status']=lp.LpSolution.get(repaired.sol_status,'Unknown')
            native=getattr(repaired,'solverModel',None) if backend=='highs' else None
            if native is not None:report['repair_native_status']=str(native.getModelStatus())
            report['repair_attempts'].append(dict(kind=kind,status=report['repair_status'],solution_status=report['repair_solution_status']))
            usable=repaired.sol_status in (lp.LpSolutionOptimal,lp.LpSolutionIntegerFeasible)
            if native is not None:usable=usable and native.getSolution().value_valid
            if not usable:continue
            # Restore original bounds/types BEFORE checking. A repaired point
            # must satisfy the original MILP, not just the auxiliary LP bounds.
            for v in variables:v.lowBound,v.upBound,v.cat=original[v.name]
            integral=max((abs(v.varValue-round(v.varValue)) for v in variables if v.isInteger()),default=0.)
            report['repair_integer_violation']=integral
            if integral>1e-6:continue
            for v in variables:
                if v.isInteger():v.varValue=round(v.varValue)
            violation=primal_violation(variables,list(repaired.constraints.values()))
            report['repair_max_primal_violation']=violation
            if violation<=1e-5:
                problem._emst_warm_start_values={v.name:float(v.varValue) for v in variables}
                report.update(candidate_available=True,repair_method=kind,changed_binary_count=sum(round(v.varValue)!=rounded[v.name] for v in variables if v.name in preferred),
                    candidate_market_objective_eur=float(lp.value(lp.lpSum(costs.get(v.name,0)*v for v in variables))+problem.objective.constant),
                    candidate_security_slack={prefix:sum(v.varValue for v in variables if v.name.startswith(prefix)) for prefix in ('energy_shortage_mwh_','energy_surplus_mwh_','reserve_shortage_mw_')})
                break
        if not report['candidate_available']:report['fallback']='cold_start_no_valid_repaired_candidate'
    except (lp.PulpSolverError,RuntimeError) as error:
        report.update(repair_status='error',error=str(error),fallback='cold_start_repair_failed')
    report['LP_relaxation_wall_seconds']=lp_seconds
    report['repair_solver_wall_seconds']=time.perf_counter()-solve_started-lp_seconds
    report['preparation_wall_seconds']=time.perf_counter()-started

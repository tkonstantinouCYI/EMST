"""Matched, fresh-process reference/compact ISP benchmark on public synthetic data."""
import argparse,json,sys,time,math,hashlib,re,subprocess,shutil,statistics,os
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def capture(data,formulation):
    from examples.synthetic_configured_fleet import build_market
    from emst import market_engine
    import emst.dispatch as dispatch_module
    from emst.solver import stage_name
    market=build_market(data,formulation);original=market_engine.dispatch;start=None;captured={}
    class Captured(Exception):pass
    def timed(*a,**kw):
        nonlocal start
        if not kw.get('rtbm',False):start=time.perf_counter()
        return original(*a,**kw)
    def intercept(problem,mip=True):
        if mip and stage_name(problem)=='ISP':
            for forced in data.get('force_states',[]):
                problem+=problem._emst_configuration_states[forced['asset']][forced['state'],forced['period']]==1
            captured.update(problem=problem,construction_seconds=time.perf_counter()-start)
            raise Captured()
        raise RuntimeError('Unexpected solve during initial ISP capture')
    try:
        with patch.object(market_engine,'dispatch',side_effect=timed),patch.object(dispatch_module,'solve',side_effect=intercept):
            market.run(time_index=market.time_index(data['day']),reserve_requirements=data['reserve_requirements'],historical_rtbm_prices={int(t):v for t,v in data['historical_rtbm_prices'].items()})
    except Captured:pass
    if 'problem' not in captured:raise RuntimeError('ISP was not captured')
    captured['market']=market
    return captured


def lp_value(x):
    import pulp
    return float(pulp.value(x))


def finite(x):return float(x) if x is not None and math.isfinite(x) else None


def highs_run(path,offset,settings,directory):
    import highspy
    highspy.Highs.resetGlobalScheduler(True)
    def instance(log=None):
        h=highspy.Highs()
        for k,v in dict(threads=settings['threads'],time_limit=settings['time_limit_seconds'],mip_rel_gap=0.,mip_abs_gap=0.,random_seed=0,primal_feasibility_tolerance=1e-9,dual_feasibility_tolerance=1e-9,mip_feasibility_tolerance=1e-9,output_flag=bool(log),log_to_console=False,parallel='on' if settings['threads']>1 else 'off').items():
            if h.setOptionValue(k,v)!=highspy.HighsStatus.kOk:raise RuntimeError('Invalid HiGHS option '+k)
        if log:
            log.unlink(missing_ok=True);h.setOptionValue('log_file',str(log))
        if h.readModel(str(path))!=highspy.HighsStatus.kOk:raise RuntimeError('Failed to read exported model')
        return h
    h=instance(directory/'highs-mip.log');first=[];root_observations=[]
    def incumbent(event):
        d=event.data_out
        if finite(d.mip_primal_bound) is not None and not first:first.append(dict(seconds=d.running_time,objective=d.mip_primal_bound+offset))
    def progress(event):
        d=event.data_out
        if d.mip_node_count==0 and finite(d.mip_dual_bound) is not None:root_observations.append(dict(seconds=d.running_time,bound=d.mip_dual_bound+offset))
    h.cbMipImprovingSolution.subscribe(incumbent);h.cbMipLogging.subscribe(progress)
    started=time.perf_counter();run_status=h.run();wall=time.perf_counter()-started;info=h.getInfo();solution=h.getSolution();lp=h.getLp()
    if run_status==highspy.HighsStatus.kError or h.getModelStatus()==highspy.HighsModelStatus.kNotset:raise RuntimeError('HiGHS failed; inspect native logs')
    # A separate continuous relaxation is the root LP before cuts/branching,
    # not a claimed post-cut root bound. No relaxation solution is reused.
    root=instance(directory/'highs-root-lp.log');root.setOptionValue('solve_relaxation',True)
    root_started=time.perf_counter();root_status=root.run();root_wall=time.perf_counter()-root_started
    root_objective=finite(root.getObjectiveValue()+offset) if root.getModelStatus()==highspy.HighsModelStatus.kOptimal else None
    text=(directory/'highs-mip.log').read_text()
    match=re.search(r'([\d.]+) \(presolve\)',text,re.I)
    initial=re.search(r'Solving MIP model with:\s+(\d+) rows\s+(\d+) cols \((\d+) binary, (\d+) integer, (\d+) implied int',text)
    reduction=re.search(r'Presolve reductions: rows (\d+).*?columns (\d+)',text)
    presolved=(dict(constraints=int(initial[1]),variables=int(initial[2]),binaries=int(initial[3]),integer_variables=sum(int(initial[k]) for k in (3,4,5)),implied_integers=int(initial[5])) if initial else dict(constraints=0,variables=0,binaries=0,integer_variables=0) if reduction and int(reduction[1])==int(reduction[2])==0 else None)
    nodes=getattr(info,'mip_node_count',None)
    values=dict(zip(lp.col_names_,solution.col_value)) if solution.value_valid else {}
    return dict(solver='highs',version=h.version(),settings=settings,warm_start='none; independent fresh models for root LP and MIP; no transferred basis or solution',
        presolved=presolved,presolve_measurement_basis='main MIP native log; total includes sub-MIP presolve',main_presolve_seconds=float(match.group(1)) if match else None,
        root_lp=dict(status=str(root.getModelStatus()),objective=root_objective,wall_seconds=root_wall,native_seconds=root.getRunTime(),basis='separate initial continuous relaxation, no cuts; includes its presolve'),
        root_search_observations=root_observations,first_incumbent=first[0] if first else None,
        status=str(h.getModelStatus()),objective=finite(h.getObjectiveValue()+offset) if solution.value_valid else None,
        best_bound=finite(getattr(info,'mip_dual_bound',None)+offset),gap=finite(getattr(info,'mip_gap',None)),
        nodes=nodes,total_solver_seconds=h.getRunTime(),wall_seconds=wall,solution=values)


def cplex_run(path,offset,settings,directory):
    executable=shutil.which('cplex')
    if not executable:return dict(solver='cplex',available=False)
    import xml.etree.ElementTree as ET
    solution=directory/'cplex.sol';commands=[f'read "{path.resolve()}"',f"set threads {settings['threads']}",f"set timelimit {settings['time_limit_seconds']}",
        'set randomseed 0','set simplex tolerances feasibility 1e-9','set simplex tolerances optimality 1e-9','set mip tolerances integrality 1e-9','set clocktype 2','set mip tolerances mipgap 0','set mip tolerances absmipgap 0','set advance 0','set parallel 1',
        'set mip display 4','set mip interval 1',f"set preprocessing repeatpresolve {settings.get('cplex_repeat_presolve',0)}",'optimize','display solution bestbound',f'write "{solution.resolve()}"','quit']
    started=time.perf_counter();first=None;optimization_started=None;lines=[]
    process=subprocess.Popen([executable,'-c',*commands],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    for line in process.stdout:
        now=time.perf_counter();lines.append(line)
        if optimization_started is None and re.search(r'CPLEX>\s*(?:optimize|mipopt|CPXPARAM_)',line,re.I):optimization_started=now
        if first is None and re.match(r'\s*\*',line) and re.search(r'\d',line):
            first=dict(wall_seconds_since_process_start=now-started,seconds_since_optimize_echo=now-optimization_started if optimization_started else None,
                basis='console arrival timestamp, includes output buffering; native exact callback time unavailable through CLI',line=line.strip())
    code=process.wait();wall=time.perf_counter()-started;text=''.join(lines);(directory/'cplex.log').write_text(text)
    if code:raise RuntimeError('CPLEX CLI failed; see cplex.log')
    attributes=ET.parse(solution).getroot().find('header').attrib if solution.exists() else {}
    def attr(name):return finite(float(attributes[name])+offset) if name in attributes else None
    presolve_text=text.split('Root relaxation solution time')[0]
    reduced=re.findall(r'Reduced MIP has ([\d,]+) rows, ([\d,]+) columns, and ([\d,]+) nonzeros',presolve_text)
    integer=re.findall(r'Reduced MIP has ([\d,]+) binaries, ([\d,]+) generals',presolve_text)
    presolve=re.findall(r'Presolve time\s*=\s*([\d.]+)',text,re.I)
    root=re.findall(r'Root relaxation solution time\s*=\s*([\d.]+)',text)
    solved=re.findall(r'Solution time\s*=\s*([\d.]+) sec\.\s*Iterations\s*=\s*([\d,]+)\s*Nodes\s*=\s*([\d,]+)',text)
    root_matches=list(re.finditer(r'Root relaxation solution time\s*=\s*([\d.]+)',text))
    native_root_duals=re.findall(r'Iteration:\s*\d+\s+Dual objective\s*=\s*([-+\deE.]+)',text[:root_matches[0].start()]) if root_matches else []
    version=re.search(r'Optimizer\s+(12\.8[^\s]*)',text)
    values={v.attrib['name']:float(v.attrib['value']) for v in ET.parse(solution).getroot().find('variables')} if solution.exists() else {}
    bound_match=re.search(r'(?:Current MIP best bound|MIP best bound)\s*=\s*([-+\deE.]+)',text,re.I)
    certified=attributes.get('solutionStatusValue')=='101'
    bound=attr('objectiveValue') if certified else float(bound_match.group(1))+offset if bound_match and abs(float(bound_match.group(1)))<1e70 else None
    objective=attr('objectiveValue')
    gap=abs(objective-bound)/(1e-10+abs(objective)) if objective is not None and bound is not None else None
    # Separate initial continuous relaxation, with no reuse in primary MIP.
    root_solution=directory/'cplex-root.sol'
    root_commands=[f'read "{path.resolve()}"',f"set threads {settings['threads']}",f"set timelimit {settings['time_limit_seconds']}",'set advance 0','set simplex tolerances feasibility 1e-9','set simplex tolerances optimality 1e-9','change problem lp','optimize',f'write "{root_solution.resolve()}"','quit']
    root_start=time.perf_counter();p=subprocess.run([executable,'-c',*root_commands],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    root_wall=time.perf_counter()-root_start;(directory/'cplex-root.log').write_text(p.stdout+p.stderr)
    root_native=re.findall(r'Solution time\s*=\s*([\d.]+) sec',p.stdout)
    root_header=ET.parse(root_solution).getroot().find('header').attrib if root_solution.exists() else {}
    return dict(solver='cplex',available=True,version=version.group(1) if version else 'see cplex.log',settings=settings,warm_start='advance 0; fresh CLI process; no MIP start',
        presolved=dict(constraints=int(reduced[-1][0].replace(',','')),variables=int(reduced[-1][1].replace(',','')),integer_variables=sum(int(x.replace(',','')) for x in integer[-1]),binaries=int(integer[-1][0].replace(',',''))) if reduced and integer and root_matches else None,
        main_presolve_seconds=sum(float(v) for v in presolve) if presolve else None,presolve_pass_seconds=[float(v) for v in presolve],first_incumbent=first,
        root_lp=dict(status=root_header.get('solutionStatusString'),objective=float(root_header['objectiveValue'])+offset if root_header.get('solutionStatusValue')=='1' else None,wall_seconds=root_wall,native_seconds=float(root_native[-1]) if root_native else None,basis='separate continuous relaxation; includes CLI startup/presolve'),
        root_relaxation_native_seconds=float(root[0]) if root else None,root_native_lp_bound=float(native_root_duals[-1])+offset if native_root_duals else None,
        objective=objective,best_bound=bound,gap=gap,
        nodes=int(solved[-1][2].replace(',','')) if solved else None,total_solver_seconds=float(solved[-1][0]) if solved else None,
        status=attributes.get('solutionStatusString','no incumbent; see cplex.log'),wall_seconds=wall,solution=values)


def worker(args):
    from examples.synthetic_configured_fleet import synthetic_case
    data=json.loads(args.inputs.read_text()) if args.inputs else synthetic_case(args.case)
    directory=args.output;directory.mkdir(parents=True,exist_ok=True)
    captured=capture(data,args.formulation);model=captured['problem'];path=directory/'model.lp';model.writeLP(str(path));model.writeMPS(str(directory/'model.mps'))
    (directory/'inputs.json').write_text(json.dumps(data,indent=2))
    settings=dict(threads=args.threads,time_limit_seconds=args.time_limit,relative_gap=0.,absolute_gap=0.,random_seed=0,primal_feasibility_tolerance=1e-9,dual_feasibility_tolerance=1e-9,integrality_tolerance=1e-9,cplex_repeat_presolve=args.cplex_repeat_presolve)
    report=dict(case=data['name'],formulation=args.formulation,input_sha256=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),
        model_lp_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),construction_seconds=captured['construction_seconds'],before_presolve=dict(variables=len(model.variables()),binaries=sum(v.isBinary() for v in model.variables()),constraints=len(model.constraints),nonzeros=sum(sum(coef!=0 for coef in c.values()) for c in model.constraints.values())),
        objective_offset=float(model.objective.constant))
    result=(highs_run if args.solver=='highs' else cplex_run)(path,report['objective_offset'],settings,directory)
    if result.get('solution'):
        solution=result.pop('solution');(directory/'solution.json').write_text(json.dumps(solution))
        report['positive_transition_starts']={k:v for k,v in solution.items() if '_transition_start_' in k and v>.5}
        for v in model.variables():v.varValue=solution.get(v.name,0.)
        from emst.optimization import primal_violation
        report['maximum_primal_violation']=primal_violation(model.variables(),list(model.constraints.values()))
        report['security_slack']={key:sum(v for name,v in solution.items() if name.startswith(key)) for key in ('energy_shortage_mwh_','energy_surplus_mwh_','reserve_shortage_mw_')}
        report['integer_violation']=max((abs(v.varValue-round(v.varValue)) for v in model.variables() if v.isInteger()),default=0.)
        report['state_schedules']={a:[next(s for (s,t),expr in z.items() if t==j and lp_value(expr)>.5) for j in range(48)] for a,z in model._emst_configuration_states.items()}
        from emst.configurations import boundary,check_rows,states,advance
        report['configuration_history_checks']={};report['ending_plant_states']={}
        for aid,sequence in report['state_schedules'].items():
            asset=captured['market'].assets[aid];specs=states(asset.parameters['plant_configurations']);names=list(specs);rows=[]
            for j,key in enumerate(sequence):
                power=sum(solution.get(f'plant_{aid}_power_{si}_{j}',0) for si in range(len(names)))
                endpoint=solution[f'plant_{aid}_end_{j}']
                rows.append(dict(configuration_state=key,configuration_binaries={s:int(s==key) for s in names},
                    power_mw=power,energy_mwh=power*.5,configuration_endpoint_mw=endpoint,hours=.5))
            initial=boundary(asset.initial_state)
            report['configuration_history_checks'][aid]=check_rows(asset,initial,rows)
            state=initial
            for row in rows:state=advance(asset,state,dict(row,end_mw=row['configuration_endpoint_mw']),30)
            report['ending_plant_states'][aid]=state
        assert all(report['configuration_history_checks'].values()),report['configuration_history_checks']
        report['new_transition_starts']={asset:[dict(period=t,transition=state.rsplit(':',1)[0]) for t,state in enumerate(sequence) if state.endswith(':0') and not (t==0 and data['plants'][next(i for i,p in enumerate(data['plants']) if p['id']==asset)]['initial_state'].get('transition'))] for asset,sequence in report['state_schedules'].items()}
    report.update(result);(directory/'metrics.json').write_text(json.dumps(report,indent=2,allow_nan=False));print(json.dumps({k:v for k,v in report.items() if k not in ('root_search_observations','reference_transition_entries')},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',choices=('small','long','fleet','forced_long'),default='small');p.add_argument('--inputs',type=Path)
    p.add_argument('--formulation',choices=('reference','compact'),default='compact');p.add_argument('--solver',choices=('highs','cplex'),default='highs')
    p.add_argument('--cplex-repeat-presolve',type=int,choices=(-1,0),default=0);p.add_argument('--threads',type=int,default=4);p.add_argument('--time-limit',type=float,default=120);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();worker(args)

if __name__=='__main__':main()

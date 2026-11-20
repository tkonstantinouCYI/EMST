"""Run the unchanged handoff inputs through reference/compact initial ISP builds."""
import argparse,hashlib,json,math,re,sys,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]


def digest(model):
    """Constraint-name-independent fingerprint of the complete mathematical MILP."""
    h=hashlib.sha256()
    def add(v):h.update(json.dumps(v,sort_keys=True).encode());h.update(b'\n')
    add([(v.name,v.cat,v.lowBound,v.upBound) for v in model.variables()])
    add([model.sense,model.objective.constant,sorted((v.name,c) for v,c in model.objective.items() if c)])
    rows=[]
    for c in model.constraints.values():
        raw=json.dumps([c.sense,c.constant,sorted((v.name,n) for v,n in c.items() if n)]).encode()
        rows.append(hashlib.sha256(raw).hexdigest())
    for row in sorted(rows):add(row)
    return h.hexdigest()


def highs(model,path,out,args,offset):
    import highspy
    first=[];root_progress=[]
    def instance(log):
        h=highspy.Highs()
        for k,v in dict(threads=args.threads,time_limit=args.seconds,presolve=args.presolve,mip_rel_gap=0.,mip_abs_gap=0.,random_seed=0,
                        log_file=str(log.resolve()),log_to_console=False).items():
            assert h.setOptionValue(k,v)==highspy.HighsStatus.kOk,k
        started=time.perf_counter();assert h.readModel(str(path.resolve()))==highspy.HighsStatus.kOk
        return h,time.perf_counter()-started
    h,read=instance(out/'solver.log')
    def incumbent(event):
        d=event.data_out
        if not first and math.isfinite(d.mip_primal_bound):first.append(dict(seconds=d.running_time,objective=d.mip_primal_bound+offset))
    def progress(event):
        d=event.data_out
        if d.mip_node_count==0 and math.isfinite(d.mip_dual_bound):root_progress.append(dict(seconds=d.running_time,bound=d.mip_dual_bound+offset))
    h.cbMipImprovingSolution.subscribe(incumbent);h.cbMipLogging.subscribe(progress)
    started=time.perf_counter();h.run();wall=time.perf_counter()-started
    info=h.getInfo();sol=h.getSolution();inc=info.primal_solution_status==2 and sol.value_valid
    def finite(v):return float(v) if math.isfinite(v) else None
    result=dict(backend='HiGHS',version=h.version(),threads=args.threads,requested_seconds=args.seconds,presolve=args.presolve,
        relative_gap_target=0,absolute_gap_target=0,random_seed=0,numerical_tolerances='HiGHS defaults, matching handoff',warm_start=False,
        read_seconds=read,solve_wall_seconds=wall,native_seconds=h.getRunTime(),status=str(h.getModelStatus()),
        incumbent_available=inc,objective=finite(info.objective_function_value+offset) if inc else None,
        best_bound=finite(info.mip_dual_bound+offset),gap=finite(info.mip_gap),nodes=info.mip_node_count,lp_iterations=info.simplex_iteration_count,
        max_native_primal_infeasibility=finite(info.max_primal_infeasibility),max_native_integrality_violation=finite(info.max_integrality_violation),
        proven_optimal=h.getModelStatus()==highspy.HighsModelStatus.kOptimal,first_incumbent=first[0] if first else None,root_progress=root_progress)
    values=dict(zip(h.getLp().col_names_,sol.col_value)) if inc else {}
    if inc:h.writeSolution(str(out/'solution.txt'),0)
    log=(out/'solver.log').read_text();presolve=re.search(r'([\d.]+) \(presolve\)',log)
    result['presolve_seconds']=float(presolve[1]) if presolve else None
    dimensions=re.search(r'Solving MIP model with:\s+(\d+) rows\s+(\d+) cols \((\d+) binary, (\d+) integer, (\d+) implied int',log)
    result['presolved']=dict(constraints=int(dimensions[1]),variables=int(dimensions[2]),binaries=int(dimensions[3])) if dimensions else None
    # A separate diagnostic relaxation, never reused as a MIP start or basis.
    root,root_read=instance(out/'relaxation.log');root.setOptionValue('solve_relaxation',True)
    started=time.perf_counter();root.run();root_wall=time.perf_counter()-started
    result['initial_LP_relaxation']=dict(status=str(root.getModelStatus()),read_seconds=root_read,wall_seconds=root_wall,native_seconds=root.getRunTime(),
        objective=root.getObjectiveValue()+offset if root.getModelStatus()==highspy.HighsModelStatus.kOptimal else None)
    return result,values


def main():
    p=argparse.ArgumentParser();p.add_argument('--formulation',choices=('reference','compact'),default='reference');p.add_argument('--baseline',type=Path)
    p.add_argument('--solver',choices=('highs','cplex','build'),default='highs');p.add_argument('--seconds',type=float,default=120);p.add_argument('--threads',type=int,default=4)
    p.add_argument('--presolve',choices=('choose','on','off'),default='choose');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.baseline:sys.path.insert(0,str(args.baseline.resolve()))
    else:sys.path.insert(0,str(ROOT))
    import emst
    # Import the unchanged generator after choosing the package implementation.
    import importlib.util
    spec=importlib.util.spec_from_file_location('handoff_inputs',ROOT/'examples/synthetic_handoff_isp.py');source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)
    args.output.mkdir(parents=True,exist_ok=False);out=args.output.resolve()
    started=time.perf_counter();kwargs,snapshot=source.build_inputs();input_seconds=time.perf_counter()-started
    for a in kwargs['assets']:
        if 'plant_configurations' in a.parameters:a.parameters['configuration_formulation']=args.formulation
    physical=json.loads(json.dumps(snapshot))
    for a in physical['assets']:a['parameters'].pop('configuration_formulation',None)
    (out/'inputs.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    class Captured(Exception):pass
    captured={}
    def capture(problem,mip=True):
        assert mip and problem.name=='library_ISP';captured.update(model=problem,construction_seconds=time.perf_counter()-started)
        raise Captured()
    started=time.perf_counter()
    with patch.object(source.dispatch_module,'solve',side_effect=capture):
        try:source.dispatch_module.dispatch(**kwargs)
        except Captured:pass
    model=captured['model'];offset=float(model.objective.constant)
    report=dict(formulation=args.formulation,implementation=str(Path(emst.__file__).resolve()),reference_revision=source.REFERENCE_REVISION,
        source_sha256=hashlib.sha256((ROOT/'examples/synthetic_handoff_isp.py').read_bytes()).hexdigest(),
        physical_inputs_sha256=hashlib.sha256(json.dumps(physical,sort_keys=True).encode()).hexdigest(),
        input_preparation_seconds=input_seconds,construction_seconds=captured['construction_seconds'],
        variables=len(model.variables()),binaries=sum(v.isBinary() for v in model.variables()),constraints=len(model.constraints),objective_offset=offset,
        model_structure_sha256=digest(model),scope='Standalone initial ISP only; no DAM, must-run auction, noon rerun, RTBM, pricing or settlement')
    started=time.perf_counter();model.writeMPS(str(out/'initial_ISP.mps'));model.writeLP(str(out/'initial_ISP.lp'));report['export_seconds']=time.perf_counter()-started
    (out/'model_size.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
    if args.solver=='build':return
    if args.solver=='highs':result,values=highs(model,out/'initial_ISP.mps',out,args,offset)
    else:
        sys.path.insert(0,str(ROOT))
        from tools.benchmark_configurations import cplex_run
        result=cplex_run(out/'initial_ISP.lp',offset,dict(threads=args.threads,time_limit_seconds=args.seconds,cplex_repeat_presolve=0),out)
        values=result.pop('solution',{});result['note']='Both CPLEX variants use strict 1e-9 tolerances, repeatpresolve 0, advance 0, deterministic parallelism; these differ from handoff CPLEX defaults.'
        import xml.etree.ElementTree as ET
        result['proven_optimal']=bool((out/'cplex.sol').exists() and ET.parse(out/'cplex.sol').getroot().find('header').get('solutionStatusValue')=='101')
    if values:
        for v in model.variables():v.varValue=values.get(v.name,0.)
        from emst.optimization import primal_violation
        result['original_model_primal_violation']=primal_violation(model.variables(),list(model.constraints.values()))
        result['original_integer_violation']=max(abs(v.varValue-round(v.varValue)) for v in model.variables() if v.isInteger())
        result['original_model_point_validated']=result['original_model_primal_violation']<=1e-5 and result['original_integer_violation']<=1e-5
        result['security_slack']={prefix:sum(v.varValue for v in model.variables() if v.name.startswith(prefix)) for prefix in ('energy_shortage_mwh_','energy_surplus_mwh_','reserve_shortage_mw_')}
        (out/'solution.json').write_text(json.dumps(values))
    report['solve']=result;(out/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps(result),flush=True)


if __name__=='__main__':main()

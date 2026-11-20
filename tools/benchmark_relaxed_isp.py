"""Synthetic conventional-thermal ISP: relax all integer variables and time LP."""
import argparse,json,math,statistics,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def inputs(count):
    units=[]
    for i in range(count):
        cap=100+10*i
        units.append(dict(id=f'thermal_{i}',participant=f'owner_{i}',capacity_mw=cap,minimum_mw=.2*cap,
            marginal_cost=55+7*i,ramp_mw_hour=3*cap,minimum_up_minutes=120,minimum_down_minutes=90,
            startup_cost=300+50*i,startup_minutes=60,shutdown_minutes=30,
            initial_state=dict(stage='dispatch' if i<6 else 'offline',power_mw=60 if i<6 else 0,time_in_stage_minutes=600)))
    return dict(schema=1,name=f'{count}_thermal_LP_relaxation',day='2026-11-05',plants=[],conventional=units,
        demand_mw=[650+150*math.sin(2*math.pi*(t-12)/48) for t in range(48)],
        res_mw=[80+150*max(0,math.sin(math.pi*(t-12)/24)) for t in range(48)],
        reserve_requirements={'FCR':{'up':[10]*48,'down':[8]*48},'aFRR':{'up':[20]*48,'down':[15]*48},'mFRR':{'up':[30]*48,'down':[20]*48}},
        historical_rtbm_prices={str(t):[[100.]*6 for _ in range(3)] for t in range(48)},force_states=[])


def worker(args):
    import pulp as lp
    from tools.benchmark_configurations import capture
    from emst.optimization import solve,primal_violation,highspy
    from emst.solver import session
    if highspy is None:raise RuntimeError('Install the optional HiGHS audit extra')
    data=inputs(args.units);captured=capture(data,'reference');model=captured['problem']
    discrete=[v for v in model.variables() if v.isInteger()]
    for v in discrete:v.cat=lp.LpContinuous
    assert not any(v.isInteger() for v in model.variables())
    started=time.perf_counter()
    with session(dict(backend='highs',threads=1,time_limit_seconds=90,relative_gap=0.,absolute_gap=0.)):
        objective=solve(model,False)
    elapsed=time.perf_counter()-started
    violation=primal_violation(model.variables(),list(model.constraints.values()));assert violation<1e-5
    fractions=[v for v in discrete if abs(v.varValue-round(v.varValue))>1e-6]
    slacks={prefix:sum(v.varValue for v in model.variables() if v.name.startswith(prefix)) for prefix in ('energy_shortage_mwh_','energy_surplus_mwh_','reserve_shortage_mw_')}
    result=dict(thermal_units=args.units,intervals=48,MTU_minutes=30,solver='HiGHS',version=highspy.Highs().version(),threads=1,
        construction_seconds=captured['construction_seconds'],LP_wall_seconds=elapsed,LP_native_seconds=model.solverModel.getRunTime(),
        construction_plus_LP_seconds=captured['construction_seconds']+elapsed,
        variables=len(model.variables()),constraints=len(model.constraints),relaxed_integer_variables=len(discrete),remaining_integer_variables=0,
        fractional_previously_integer_variables=len(fractions),objective_eur=objective,maximum_primal_violation=violation,
        security_slack=slacks,status=str(model.solverModel.getModelStatus()),
        note='Pure LP relaxation of the initial full-day ISP. No integer rounding, fixed-commitment pricing, noon run, RTBM or settlement.')
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'inputs.json').write_text(json.dumps(data,indent=2));model.writeLP(str(args.output/'relaxed_ISP.lp'))
    (args.output/'solution.json').write_text(json.dumps({v.name:v.varValue for v in model.variables()}))
    (args.output/'metrics.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--units',type=int,default=10);p.add_argument('--repetitions',type=int,default=5)
    p.add_argument('--worker',action='store_true');p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.worker:return worker(args)
    args.output.mkdir(parents=True,exist_ok=True);runs=[]
    for i in range(args.repetitions):
        directory=args.output/f'run-{i+1}'
        proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker','--units',str(args.units),'--output',str(directory)],capture_output=True,text=True)
        if proc.returncode:raise RuntimeError(proc.stdout+proc.stderr)
        runs.append(json.loads((directory/'metrics.json').read_text()))
    summary=dict(passed=True,repetitions=len(runs),fresh_processes=True,runs=runs,
        medians={k:statistics.median(r[k] for r in runs) for k in ('construction_seconds','LP_wall_seconds','LP_native_seconds','construction_plus_LP_seconds')})
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))


if __name__=='__main__':main()

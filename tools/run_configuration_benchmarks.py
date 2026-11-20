"""Run matched benchmarks sequentially, in alternating formulation order."""
import argparse,json,subprocess,sys,statistics,csv,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cplex-repeat-presolve',type=int,choices=(-1,0),default=0);p.add_argument('--output',type=Path,required=True);p.add_argument('--threads',type=int,default=4);p.add_argument('--time-limit',type=float,default=120)
    p.add_argument('--repetitions',default='small:3,long:2,forced_long:1,fleet:1');p.add_argument('--solvers',nargs='+',choices=('highs','cplex'),default=['highs','cplex'])
    args=p.parse_args()
    if 'cplex' in args.solvers and shutil.which('cplex') is None:
        args.solvers.remove('cplex');print('CPLEX CLI unavailable; running HiGHS only',flush=True)
    if not args.solvers:p.error('No requested solver is available')
    args.output.mkdir(parents=True,exist_ok=True);runs=[]
    for part in args.repetitions.split(','):
        case,count=part.split(':')
        for repeat in range(int(count)):
            for solver in args.solvers:
                for variant in (('reference','compact') if repeat%2==0 else ('compact','reference')):
                    directory=args.output/f'{case}-{solver}-{repeat+1}-{variant}'
                    command=[sys.executable,str(ROOT/'tools/benchmark_configurations.py'),'--case',case,'--solver',solver,'--formulation',variant,
                        '--cplex-repeat-presolve',str(args.cplex_repeat_presolve),'--threads',str(args.threads),'--time-limit',str(args.time_limit),'--output',str(directory)]
                    print('Starting',directory.name,flush=True)
                    process=subprocess.run(command,capture_output=True,text=True)
                    (directory/'worker.stdout.log').write_text(process.stdout+process.stderr)
                    if process.returncode:raise RuntimeError(directory.name+': '+process.stdout+process.stderr)
                    result=json.loads((directory/'metrics.json').read_text());result.update(repeat=repeat+1,directory=str(directory));runs.append(result)
                    (args.output/'runs.json').write_text(json.dumps(runs,indent=2))
                    print('Completed',directory.name,'status=',result.get('status'),'seconds=',result.get('total_solver_seconds'),'gap=',result.get('gap'),flush=True)
    groups={};comparisons={}
    for r in runs:groups.setdefault((r['case'],r['solver'],r['formulation']),[]).append(r)
    rows=[]
    for (case,solver,variant),group in groups.items():
        record=dict(case=case,solver=solver,formulation=variant,repetitions=len(group),statuses=[g.get('status') for g in group],before_presolve=group[0]['before_presolve'],presolved=group[0].get('presolved'),
            median_construction_seconds=statistics.median(g['construction_seconds'] for g in group),median_solver_seconds=statistics.median(g['total_solver_seconds'] for g in group if g.get('total_solver_seconds') is not None) if any(g.get('total_solver_seconds') is not None for g in group) else None,
            final_objectives=[g.get('objective') for g in group],bounds=[g.get('best_bound') for g in group],gaps=[g.get('gap') for g in group],nodes=[g.get('nodes') for g in group],
            security_feasible=[all(abs(q)<1e-6 for q in g.get('security_slack',{}).values()) if g.get('objective') is not None else None for g in group],
            root_lp_bounds=[g['root_lp'].get('objective') for g in group],root_lp_seconds=[g['root_lp']['wall_seconds'] for g in group],
            presolve_seconds=[g.get('main_presolve_seconds') for g in group],first_incumbents=[g.get('first_incumbent') for g in group])
        rows.append(record)
    for case,solver,_ in groups:
        key=case+'-'+solver
        if key in comparisons:continue
        a=groups[case,solver,'reference'];b=groups[case,solver,'compact']
        assert all(x['input_sha256']==y['input_sha256'] and x.get('settings')==y.get('settings') for x,y in zip(a,b))
        pair=[]
        for x,y in zip(a,b):
            both_optimal=x.get('gap')==0 and y.get('gap')==0 and x.get('objective') is not None and y.get('objective') is not None
            gap=abs(x['objective']-y['objective']) if both_optimal else None
            if both_optimal and gap>1e-3:raise AssertionError((key,'different certified objectives',gap))
            root=(y['root_lp']['objective']-x['root_lp']['objective']) if y['root_lp']['objective'] is not None and x['root_lp']['objective'] is not None else None
            if root is not None and root<-1e-3:raise AssertionError((key,'weaker root relaxation',root))
            mismatches=sum(s!=t for asset,states in x.get('state_schedules',{}).items() for s,t in zip(states,y.get('state_schedules',{}).get(asset,[]))) if x.get('state_schedules') and y.get('state_schedules') else None
            pair.append(dict(both_certified_optimal=both_optimal,objective_difference=gap,compact_minus_reference_root_bound=root,state_mismatches=mismatches,
                interpretation='same optimal value; differing feasible schedules are alternative optima, see solution lifting and symmetry notes' if both_optimal and mismatches else 'same optimal value and state sequence' if both_optimal else 'time-limited/uncertified; no optimal-objective equality claim'))
        comparisons[key]=pair
    (args.output/'summary.json').write_text(json.dumps(dict(groups=rows,comparisons=comparisons),indent=2))
    with (args.output/'summary.csv').open('w',newline='') as file:
        writer=csv.writer(file);writer.writerow(['case','solver','formulation','repetitions','variables','binaries','constraints','construction_seconds','solver_seconds','objective','bound','gap','nodes'])
        for g in rows:writer.writerow([g['case'],g['solver'],g['formulation'],g['repetitions'],*[g['before_presolve'][k] for k in ('variables','binaries','constraints')],g['median_construction_seconds'],g['median_solver_seconds'],g['final_objectives'],g['bounds'],g['gaps'],g['nodes']])
    print('Saved',args.output/'summary.json',flush=True)

if __name__=='__main__':main()

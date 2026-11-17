"""Pair baseline/current days in fresh processes, without simulation/export overlap."""
import argparse,json,statistics,subprocess,sys,platform
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--worker-source',type=Path)
    parser.add_argument('--equal',action='store_true')
    parser.add_argument('--save-results',action='store_true')
    args=parser.parse_args()
    if args.worker_source:
        sys.path.insert(0,str(args.worker_source.resolve()))
        import emst
        from emst import market_engine
        from emst.optimization import solve,highspy
        import pulp as lp
        # Load the selected implementation before importing the shared harness.
        assert Path(emst.__file__).resolve().is_relative_to(args.worker_source.resolve())
        sys.path.insert(0,str(ROOT/'tools'))
        from benchmark_storage_activation import run_case
        warm=lp.LpProblem('warmup',lp.LpMinimize);x=lp.LpVariable('warm_x',0);warm+=x;warm+=x>=1
        solve(warm,False)
        result,row=run_case(args.equal)
        if args.save_results:
            directory=args.output.parent/args.output.stem
            result.save(directory/'raw')
            result.save_readable(directory)
        row['environment']=dict(python=platform.python_version(),pulp=lp.__version__,highs=highspy.Highs().version() if highspy is not None else None)
        args.output.write_text(json.dumps(row,allow_nan=False),encoding='utf-8')
        return
    if args.baseline_root is None:parser.error('baseline-root is required')
    if args.repetitions<1:parser.error('repetitions must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    report=dict(methodology='fresh process per measured day; modules and tiny LP warmed; alternating pair order; no concurrent simulations or large exports; Market.run wall time',cases={})
    for repeat in range(args.repetitions):
        for case,equal in (('distinct_prices',False),('equal_prices',True)):
            order=('before','after') if repeat%2==0 else ('after','before')
            data=report['cases'].setdefault(case,dict(before=[],after=[]))
            for variant in order:
                source=args.baseline_root if variant=='before' else ROOT
                output=args.output/f'{case}-{repeat+1}-{variant}.json'
                cmd=[sys.executable,str(Path(__file__).resolve()),'--worker-source',str(source),'--output',str(output)]
                if equal:cmd.append('--equal')
                process=subprocess.run(cmd,capture_output=True,text=True)
                if process.returncode:raise RuntimeError(process.stdout+'\n'+process.stderr)
                row=json.loads(output.read_text());data[variant].append(row)
                print(f'{case} pair {repeat+1} {variant}: {row["seconds"]:.3f}s',flush=True)
            before=data['before'][-1];after=data['after'][-1]
            assert before['environment']==after['environment']
            report['environment']=after['environment']
            assert before['scenario_fingerprint']==after['scenario_fingerprint']
            assert before['implementation_fingerprint']!=after['implementation_fingerprint']
            assert all(r['audit_passed'] and r['feasible_without_security_slack'] for r in (before,after))
            assert max(after['maximum_simultaneous_activation_mwh'].values())<=1e-7
            data['before_median_seconds']=statistics.median(r['seconds'] for r in data['before'])
            data['after_median_seconds']=statistics.median(r['seconds'] for r in data['after'])
            data['change_percent']=(data['after_median_seconds']/data['before_median_seconds']-1)*100
            data['after_maximum_simultaneous_mwh']={s:max(r['maximum_simultaneous_activation_mwh'][s] for r in data['after']) for s in ('ISP','RTBM')}
            for variant in ('before','after'):
                data[variant+'_median_solver_seconds_by_stage']={s:statistics.median(r['solver_seconds_by_stage'].get(s,0) for r in data[variant]) for s in data[variant][0]['solver_seconds_by_stage']}
            report['passed']=all(len(v['before'])==args.repetitions and len(v['after'])==args.repetitions for v in report['cases'].values()) and len(report['cases'])==2
            (args.output/'comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')

    for case,equal in (('distinct_prices',False),('equal_prices',True)):
        output=args.output/(case+'-validated-day.json')
        cmd=[sys.executable,str(Path(__file__).resolve()),'--worker-source',str(ROOT),'--output',str(output),'--save-results']
        if equal:cmd.append('--equal')
        process=subprocess.run(cmd,capture_output=True,text=True)
        if process.returncode:raise RuntimeError(process.stdout+'\n'+process.stderr)
        print(case+': raw/readable export rerun passed',flush=True)


if __name__=='__main__':main()

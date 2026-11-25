"""Serial matched native half-hour/hour ON/OFF daily benchmarks, synthetic data."""
import argparse,json,platform,statistics,sys,time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pulp as lp
import highspy
from examples.onoff_day import build_market
from emst.solver import configuration
import emst.market_engine as engine
import emst.dispatch as module


def worker(minutes,units,directory):
    directory.mkdir(parents=True,exist_ok=False)
    market,requirements,history=build_market(units)
    market.solver_config=configuration(dict(backend='highs',threads=4,time_limit_seconds=90,relative_gap=0,absolute_gap=0))
    original=engine.dispatch;measurements=[]
    def timed(*args,**kw):
        if kw.get('rtbm'):return original(*args,**kw)
        start=time.perf_counter();calls=[];sizes={};solve=module.solve
        def measured(problem,mip=True):
            if mip:
                sizes.update(variables=len(problem.variables()),binaries=sum(v.isBinary() for v in problem.variables()),constraints=len(problem.constraints))
                sizes['build_seconds']=time.perf_counter()-start
            tick=time.perf_counter();value=solve(problem,mip)
            calls.append(dict(mip=mip,seconds=time.perf_counter()-tick))
            return value
        with patch.object(module,'solve',side_effect=measured):result=original(*args,**kw)
        total=time.perf_counter()-start
        measurements.append(dict(**sizes,intervals=len(args[2]),total_seconds=total,
            milp_seconds=sum(c['seconds'] for c in calls if c['mip']),pricing_seconds=sum(c['seconds'] for c in calls if not c['mip']),
            other_seconds=total-sizes['build_seconds']-sum(c['seconds'] for c in calls)))
        return result
    started=time.perf_counter()
    with patch.object(engine,'dispatch',side_effect=timed):
        result=market.run(time_index=market.time_index('2026-11-05'),reserve_requirements=requirements,
            historical_rtbm_prices=history,isp_interval_minutes=minutes,rtbm_interval_minutes=30)
    daily=time.perf_counter()-started
    assert result.audit['passed'],result.audit
    assert all(v['reconciled'] for v in result.economics['participants'].values())
    assert [m['binaries'] for m in measurements]==[units*1440//minutes,units*720//minutes]
    export=time.perf_counter();result.save(directory/'raw');result.save_readable(directory)
    report=dict(isp_minutes=minutes,units=units,ISP=measurements,total_day_seconds=daily,
        validation_seconds=result.manifest['validation_timing']['wall_seconds'],export_seconds=time.perf_counter()-export,
        stage_checks_passed=True,cash_reconciled=True,physical_security_feasible=result.manifest['physical_security_feasible'],
        delivery_residual=result.diagnostics['actual_balance'],
        objective_eur=[r['milp_objective_eur'] for r in result.isp_runs],
        solver_records=result.diagnostics['solve_records'])
    (directory/'timing.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(minutes=minutes,daily_seconds=daily,ISP=measurements)),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--units',type=int,default=13)
    p.add_argument('--repetitions',type=int,default=3);p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.units<2 or args.repetitions<1:p.error('At least two units and one repetition are required')
    out=args.output or ROOT/'output'/('onoff-isp-benchmark-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,exist_ok=False);runs=[]
    for repeat in range(args.repetitions):
        for minutes in ((30,60) if repeat%2==0 else (60,30)):
            runs.append(worker(minutes,args.units,out/f'repeat-{repeat}-isp-{minutes}'))
    summary={str(minutes):dict(ISP=[{key:statistics.median(r['ISP'][index][key] for r in runs if r['isp_minutes']==minutes)
        for key in ('build_seconds','milp_seconds','pricing_seconds','total_seconds','binaries','variables','constraints')} for index in (0,1)],
        median_day_seconds=statistics.median(r['total_day_seconds'] for r in runs if r['isp_minutes']==minutes),
        median_total_ISP_seconds=statistics.median(sum(x['total_seconds'] for x in r['ISP']) for r in runs if r['isp_minutes']==minutes),
        median_validation_seconds=statistics.median(r['validation_seconds'] for r in runs if r['isp_minutes']==minutes)) for minutes in (30,60)}
    report=dict(passed=True,summary=summary,runs=runs,conditions=dict(python=platform.python_version(),pulp=lp.__version__,
        highs=highspy.Highs().version(),threads=4,time_limit_seconds=90,absolute_gap=0,relative_gap=0,warm_start=False,
        numerical_tolerances='Identical HiGHS defaults',RTBM_minutes=30,
        interpretation='Two physical approximations, not an equivalent grid reformulation. All inputs synthetic; validation/exports excluded from ISP timings.'))
    (out/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),summary=summary)),flush=True)


if __name__=='__main__':main()

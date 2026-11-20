"""Fresh-process daily cold/warm comparison, including repair and settlement."""
import argparse,json,statistics,subprocess,sys,time,platform
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def worker(args):
    from emst.solver import configuration
    import emst.market_engine as engine
    import emst.dispatch as dispatch_module
    if args.case=='storage':
        from examples.library_day import build_market
        m,index,reserves,history=build_market(storage_energy=True,storage_capacity=True)
        inputs=dict(time_index=index,reserve_requirements=reserves,historical_rtbm_prices=history,must_run_requirement_mw=[180.]*48)
    elif args.case in ('synthetic','thermal10'):
        from examples.synthetic_configured_fleet import build_market,synthetic_case
        if args.case=='thermal10':
            from tools.benchmark_relaxed_isp import inputs as thermal_inputs
            data=thermal_inputs(10)
        else:data=synthetic_case('small')
        m=build_market(data,'compact')
        inputs=dict(time_index=m.time_index(data['day']),reserve_requirements=data['reserve_requirements'],historical_rtbm_prices={int(k):v for k,v in data['historical_rtbm_prices'].items()})
    else:
        from examples.configured_plant import build_market
        m=build_market(configuration_formulation='compact')
        inputs=dict(time_index=m.time_index('2026-11-05'),historical_rtbm_prices={i:[[60.]*6]*3 for i in range(48)})
    inputs['rtbm_interval_minutes']=args.minutes
    # All primary solves retain the same limit, zero gaps, one thread, no logging.
    m.solver_config=configuration(dict(backend='highs',threads=1,time_limit_seconds=90,relative_gap=0.,absolute_gap=0.,
        stages={'ISP':dict(warm_start=args.mode!='cold',warm_start_source='lp' if args.mode=='lp' else 'schedule',warm_start_repair_seconds=args.repair_seconds)}))
    if args.seed:inputs['isp_warm_start']=json.loads(args.seed.read_text())
    stages=[];counts=[];original=engine.dispatch;original_solve=dispatch_module.solve
    def capture_solve(problem,mip=True):
        if mip and problem.name=='library_ISP':counts.append(dict(variables=len(problem.variables()),binaries=sum(v.isBinary() for v in problem.variables()),constraints=len(problem.constraints)))
        return original_solve(problem,mip)
    def measured(*a,**kw):
        started=time.perf_counter();result=original(*a,**kw)
        if not kw.get('rtbm',False):stages.append(time.perf_counter()-started)
        return result
    started=time.perf_counter()
    with patch.object(engine,'dispatch',side_effect=measured),patch.object(dispatch_module,'solve',side_effect=capture_solve):r=m.run(**inputs)
    total=time.perf_counter()-started
    assert r.audit['passed']
    if args.case in ('storage','configured'):assert r.manifest['physical_security_feasible']
    assert all(v['reconciled'] for v in r.economics['participants'].values())
    records=[v for v in r.diagnostics['solve_records'] if v['stage']=='ISP' and v['purpose']=='market_clearing']
    assert len(records)==2 and all(v['termination_reason']=='optimal' for v in records)
    for rec in records:
        assert rec['effective_options']['relative_gap']==rec['effective_options']['absolute_gap']==0
    args.output.mkdir(parents=True,exist_ok=True);r.save(args.output/'raw');r.save_readable(args.output)
    (args.output/'seed.json').write_text(json.dumps(r.isp_runs[0]))
    readable=r.to_readable(audience='operator')['simulation_summary']['days'][0]['market_stages']
    metrics=dict(case=args.case,minutes=args.minutes,mode=args.mode,python=platform.python_version(),solver_version=records[0]['backend_version'],
        total_day_wall_seconds=total,ISP_dispatch_wall_seconds=stages,primary_MILP_seconds=[v['wall_seconds'] for v in records],
        repair_wall_seconds=[v.get('warm_start',{}).get('preparation_wall_seconds',0.) for v in records],
        LP_guidance_wall_seconds=[v.get('warm_start',{}).get('LP_relaxation_wall_seconds',0.) for v in records],
        warm_start=[v.get('warm_start') for v in records],model_sizes=counts,
        ISP_objectives=[v['milp_objective_eur'] for v in r.isp_runs],
        all_stages_passed=r.audit['passed'],security_feasible=r.manifest['physical_security_feasible'],participant_cash_reconciled=True,
        ISP_security_slack=[{key:sum(row['slack'][key] for row in run['rows']) for key in ('energy_shortage_mwh','energy_surplus_mwh')} for run in r.isp_runs],
        RTBM_security_slack={key:sum(row['slack'][key] for row in r.rtbm) for key in ('energy_shortage_mwh','energy_surplus_mwh')},
        stage_solver_wall_seconds=r.diagnostics['solver_wall_seconds_by_stage'],
        cash={owner:v['financial_position']['daily_total_cash_eur'] for owner,v in readable['settlement']['participants'].items()},
        effective_primary_options=records[0]['effective_options'])
    (args.output/'metrics.json').write_text(json.dumps(metrics,indent=2));print(json.dumps(metrics),flush=True)


def compare(cold,warm):
    from decimal import Decimal
    a=json.loads((Path(cold['directory'])/'raw/rtbm.json').read_text());b=json.loads((Path(warm['directory'])/'raw/rtbm.json').read_text())
    diff=[abs(x-y) for x,y in zip(cold['ISP_objectives'],warm['ISP_objectives'])]
    assert max(diff)<1e-3,(cold['case'],cold['minutes'],warm['mode'],diff)
    assert cold['model_sizes']==warm['model_sizes']
    cash={owner:str(Decimal(v)-Decimal(cold['cash'][owner])) for owner,v in warm['cash'].items()}
    return dict(case=cold['case'],minutes=cold['minutes'],mode=warm['mode'],repeat=cold['repeat'],ISP_objective_differences=diff,
        identical_model_dimensions=True,all_stages_passed=True,participant_cash_reconciled=True,security_slack_used=not (cold['security_feasible'] and warm['security_feasible']),
        maximum_dispatch_difference_mw=max(abs(x['assets'][aid]['power_mw']-y['assets'][aid]['power_mw']) for x,y in zip(a,b) for aid in x['assets']),
        maximum_RTBM_price_difference_eur_mwh=max(abs(x['price_eur_mwh']-y['price_eur_mwh']) for x,y in zip(a,b)),
        maximum_settlement_price_difference_eur_mwh=max(abs(x['settlement_price_eur_mwh']-y['settlement_price_eur_mwh']) for x,y in zip(a,b)),
        participant_cash_difference_eur=cash)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker',action='store_true');p.add_argument('--case',choices=('storage','configured','synthetic','thermal10'),default='storage')
    p.add_argument('--cases',nargs='+',choices=('storage','configured','synthetic','thermal10'),default=['storage','configured'])
    p.add_argument('--modes',nargs='+',choices=('cold','boundary','reused','lp'),default=['cold','boundary','reused'])
    p.add_argument('--resolutions',nargs='+',type=int,choices=(5,30),default=[5,30])
    p.add_argument('--minutes',type=int,choices=(5,30),default=5);p.add_argument('--mode',choices=('cold','boundary','reused','lp'),default='cold');p.add_argument('--seed',type=Path)
    p.add_argument('--repair-seconds',type=float,default=5.);p.add_argument('--repetitions',type=int,default=3);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.worker:return worker(args)
    args.output.mkdir(parents=True,exist_ok=True);runs=[];comparisons=[]
    # Prepare historical seeds separately. Their cost is reported, not hidden
    # in the warm-run time. Reused mode tests an ideal unchanged prior forecast.
    seeds={};bootstrap=[]
    def run(case,minutes,mode,repeat,seed=None,prefix='run'):
        folder=args.output/f'{prefix}-{case}-{minutes}-{mode}-{repeat}'
        cmd=[sys.executable,str(Path(__file__).resolve()),'--worker','--case',case,'--minutes',str(minutes),'--mode',mode,'--repair-seconds',str(args.repair_seconds),'--output',str(folder)]
        if seed:cmd+=['--seed',str(seed)]
        print('Starting',folder.name,flush=True)
        proc=subprocess.run(cmd,capture_output=True,text=True)
        folder.mkdir(parents=True,exist_ok=True);(folder/'worker.log').write_text(proc.stdout+proc.stderr)
        if proc.returncode:raise RuntimeError(folder.name+': '+proc.stdout+proc.stderr)
        result=json.loads((folder/'metrics.json').read_text());result.update(directory=str(folder),repeat=repeat)
        print('Finished',folder.name,'day s=',round(result['total_day_wall_seconds'],3),'ISP s=',[round(v,3) for v in result['ISP_dispatch_wall_seconds']],flush=True)
        return result
    for case in args.cases:
        for minutes in args.resolutions:
            boot=run(case,minutes,'cold',0,prefix='seed');seeds[case,minutes]=Path(boot['directory'])/'seed.json';bootstrap.append(boot)
            for repeat in range(1,args.repetitions+1):
                group={}
                order=args.modes if repeat%2 else list(reversed(args.modes))
                for mode in order:
                    group[mode]=run(case,minutes,mode,repeat,seeds[case,minutes] if mode=='reused' else None);runs.append(group[mode])
                    (args.output/'runs.json').write_text(json.dumps(runs,indent=2))
                for mode in args.modes:
                    if mode!='cold':comparisons.append(compare(group['cold'],group[mode]))
    groups=[]
    for case in args.cases:
        for minutes in args.resolutions:
            for mode in args.modes:
                g=[r for r in runs if (r['case'],r['minutes'],r['mode'])==(case,minutes,mode)]
                groups.append(dict(case=case,minutes=minutes,mode=mode,repetitions=len(g),median_day_seconds=statistics.median(v['total_day_wall_seconds'] for v in g),
                    median_ISP_dispatch_seconds=[statistics.median(v['ISP_dispatch_wall_seconds'][i] for v in g) for i in (0,1)],
                    median_primary_MILP_seconds=[statistics.median(v['primary_MILP_seconds'][i] for v in g) for i in (0,1)],
                    median_repair_seconds=[statistics.median(v['repair_wall_seconds'][i] for v in g) for i in (0,1)],
                    median_LP_guidance_seconds=[statistics.median(v['LP_guidance_wall_seconds'][i] for v in g) for i in (0,1)],
                    candidates_submitted=sum(bool(w and w['candidate_available'] and w['submission_status']=='HighsStatus.kOk') for r in g for w in r['warm_start'])))
    (args.output/'summary.json').write_text(json.dumps(dict(passed=True,groups=groups,comparisons=comparisons,seed_generation=bootstrap,
        conditions='Fresh sequential workers; HiGHS, one thread, 90s primary solve limit, zero absolute/relative gaps, 5s total repair budget. Reused mode seeds are pre-existing identical-forecast solves; their bootstrap cost is reported separately.'),indent=2))
    print('Saved',args.output/'summary.json',flush=True)


if __name__=='__main__':main()

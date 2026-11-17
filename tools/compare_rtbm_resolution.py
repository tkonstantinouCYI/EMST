"""Compare 5/30-minute sequential RTBM on identical inputs in fresh processes."""
import argparse,json,statistics,subprocess,sys,time,platform
from dataclasses import asdict
from decimal import Decimal,localcontext
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from examples.library_day import build_market
from emst.evaluation import fingerprint


def evaluate(minutes,output,save=False):
    from emst.optimization import solve,highspy
    import pulp as lp
    m,index,reserves,history=build_market(storage_energy=True,storage_capacity=True)
    signature=fingerprint(dict(assets=[asdict(a) for a in m.assets.values()],participants=[asdict(p) for p in m.participants.values()],
        forwards=[asdict(f) for f in m.forwards],index=[t.isoformat() for t in index],rules=m.ruleset.to_dict(),
        reserves=reserves,history=history,must_run_requirement_mw=[180.]*48,solver=m.solver_config,settlement=m.settlement_parameters))
    warm=lp.LpProblem('warmup',lp.LpMinimize);x=lp.LpVariable('warm_x',0);warm+=x;warm+=x>=1;solve(warm,False)
    started=time.perf_counter()
    r=m.run(time_index=index,reserve_requirements=reserves,historical_rtbm_prices=history,
        must_run_requirement_mw=[180.]*48,rtbm_interval_minutes=minutes)
    seconds=time.perf_counter()-started
    assert r.audit['passed'] and r.manifest['physical_security_feasible']
    assert len(r.rtbm)==24*60//minutes and len(r.isp_runs)==2
    assert all(p['reconciled'] for p in r.economics['participants'].values())
    readable=r.to_readable(audience='operator')['simulation_summary']['days'][0]['market_stages']
    with localcontext() as context:
        context.prec=80
        for owner,data in readable['settlement']['participants'].items():
            cash=sum((Decimal(e['cash_eur'])*(int(e['account'].startswith(f'participant:{owner}:'))-int(e['counterparty'].startswith(f'participant:{owner}:'))) for e in r.ledger),Decimal(0))
            assert cash==Decimal(data['financial_position']['daily_total_cash_eur'])
    dispatch_assets=[a.id for a in m.assets.values() if a.technology in ('thermal','storage')]
    batches=[[row for row in r.rtbm if row['period']==t] for t in range(48)]
    metrics=dict(interval_minutes=minutes,seconds=seconds,scenario_fingerprint=signature,
        implementation_fingerprint=r.manifest['implementation_fingerprint'],
        environment=dict(python=platform.python_version(),pulp=lp.__version__,highs=highspy.Highs().version() if highspy else None),
        solver_seconds_by_stage=r.diagnostics['solver_wall_seconds_by_stage'],audit=r.audit,
        stages=dict(DAM_mtus=48,ISP_runs=len(r.isp_runs),RTBM_intervals=len(r.rtbm),settlement_meters=len(r.meters)),
        first_ISP_objective_eur=r.isp_runs[0]['milp_objective_eur'],second_ISP_objective_eur=r.isp_runs[1]['milp_objective_eur'],
        DAM_prices_eur_mwh=r.dam['prices_eur_mwh'],DAM_accepted_mwh=r.dam['accepted_mwh'],
        dispatch_mw_by_mtu={aid:[sum(row['assets'][aid]['actual_mwh'] for row in batch)/.5 for batch in batches] for aid in dispatch_assets},
        actual_energy_mwh_by_asset={a.id:sum(v['actual_mwh'] for v in r.meters if v['asset']==a.id) for a in m.assets.values()},
        RTBM_time_average_price_by_mtu=[sum(row['price_eur_mwh']*row['hours'] for row in batch)/.5 for batch in batches],
        settlement_price_by_mtu=[batch[0]['settlement_price_eur_mwh'] for batch in batches],
        LABEO_by_mtu=readable['settlement']['summary']['intervals'],
        financial_positions={p:v['financial_position']['totals'] for p,v in readable['settlement']['participants'].items()},
        ending_states=r.ending_states,physical_operating_cost_eur=r.economics['physical_operating_cost_eur'],
        actual_balance=r.diagnostics['actual_balance'],settlement_resolution=r.manifest['settlement_resolution'],
        all_cash_reconciled=True,no_security_slack=True,
        information_basis=r.manifest['actual_observation_basis'],ramping=r.manifest['rtbm_ramping'])
    if save:
        directory=output.parent/output.stem
        r.save(directory/'raw');r.save_readable(directory)
    output.write_text(json.dumps(metrics,indent=2,allow_nan=False),encoding='utf-8')


def difference(a,b):
    values=[y-x for x,y in zip(a,b)]
    return dict(mean_absolute_difference=statistics.mean(abs(v) for v in values),maximum_absolute_difference=max(abs(v) for v in values),
        mean_5min=statistics.mean(a),mean_30min=statistics.mean(b),differences=values)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--worker-minutes',type=int,choices=(5,30))
    parser.add_argument('--save',action='store_true')
    args=parser.parse_args()
    if args.worker_minutes:
        evaluate(args.worker_minutes,args.output,args.save);return
    if args.repetitions<1:parser.error('repetitions must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    report=dict(passed=False,methodology='fresh process per run; modules/tiny LP warmed; alternating order; no concurrent simulations/exports; Market.run wall time',timings={'5':[],'30':[]})
    for repeat in range(args.repetitions):
        for minutes in ((5,30) if repeat%2==0 else (30,5)):
            target=args.output/f'{minutes}min-run-{repeat+1}.json'
            process=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker-minutes',str(minutes),'--output',str(target)],capture_output=True,text=True)
            if process.returncode:raise RuntimeError(process.stdout+'\n'+process.stderr)
            data=json.loads(target.read_text());report['timings'][str(minutes)].append(data['seconds'])
            print(f'pair {repeat+1}, {minutes} minutes: {data["seconds"]:.3f}s',flush=True)
        fine=json.loads((args.output/f'5min-run-{repeat+1}.json').read_text())
        coarse=json.loads((args.output/f'30min-run-{repeat+1}.json').read_text())
        assert fine['scenario_fingerprint']==coarse['scenario_fingerprint']
        assert fine['implementation_fingerprint']==coarse['implementation_fingerprint']
        assert fine['environment']==coarse['environment']
        assert max(abs(x-y) for x,y in zip(fine['DAM_prices_eur_mwh'],coarse['DAM_prices_eur_mwh']))<1e-7
        assert max(abs(v-coarse['DAM_accepted_mwh'][k]) for k,v in fine['DAM_accepted_mwh'].items())<1e-7
        assert abs(fine['first_ISP_objective_eur']-coarse['first_ISP_objective_eur'])<1e-3
    # Compare representative first-pair results; retain every repetition above.
    fine=json.loads((args.output/'5min-run-1.json').read_text());coarse=json.loads((args.output/'30min-run-1.json').read_text())
    report.update(environment=fine['environment'],median_seconds={k:statistics.median(v) for k,v in report['timings'].items()},
        dispatch_mw_comparison={aid:difference(values,coarse['dispatch_mw_by_mtu'][aid]) for aid,values in fine['dispatch_mw_by_mtu'].items()},
        RTBM_price_comparison_eur_mwh=difference(fine['RTBM_time_average_price_by_mtu'],coarse['RTBM_time_average_price_by_mtu']),
        settlement_price_comparison_eur_mwh=difference(fine['settlement_price_by_mtu'],coarse['settlement_price_by_mtu']),
        information_and_ramping_assumptions={'5':'forecast plus last completed observed error; physical ramps','30':'current participant demand/RES inputs known at start; immediate ramping'},
        economic_comparison_basis='first timed pair; all repetitions retained',
        DAM_and_initial_ISP_objective_unchanged=True,actual_energy_mwh={'5':fine['actual_energy_mwh_by_asset'],'30':coarse['actual_energy_mwh_by_asset']},
        ending_states={'5':fine['ending_states'],'30':coarse['ending_states']},
        physical_operating_cost_eur={'5':fine['physical_operating_cost_eur'],'30':coarse['physical_operating_cost_eur']},
        actual_balance={'5':fine['actual_balance'],'30':coarse['actual_balance']},financial_position_comparison={})
    with localcontext() as context:
        context.prec=80
        for owner,totals in fine['financial_positions'].items():
            other=coarse['financial_positions'][owner]
            report['financial_position_comparison'][owner]=dict(cash_5min=totals,cash_30min=other,
                changes={k:str(Decimal(other[k])-Decimal(v)) for k,v in totals.items()})
    report['speedup']=report['median_seconds']['5']/report['median_seconds']['30']
    report['passed']=True
    (args.output/'comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    # Large exports follow all measured runs.
    for minutes in (5,30):
        process=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker-minutes',str(minutes),
            '--output',str(args.output/f'{minutes}min-results.json'),'--save'],capture_output=True,text=True)
        if process.returncode:raise RuntimeError(process.stdout+'\n'+process.stderr)
        print(f'{minutes} minutes: raw/readable validation export passed',flush=True)
    report['exports_complete']=True
    (args.output/'comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':main()

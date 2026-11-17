"""Sequential daily benchmarks with distinct/equal storage activation prices."""
import argparse,json,platform,statistics,sys,time
from decimal import Decimal,localcontext
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from examples.library_day import build_market
from emst import Strategy
from emst.evaluation import fingerprint
from emst.optimization import highspy


def run_case(equal):
    market,index,reserves,history=build_market(storage_energy=True,storage_capacity=True)
    if equal:
        asset=market.assets['battery']
        asset.strategy=Strategy(asset.strategy.name,{**asset.strategy.parameters,
            'balancing_up_price':100.,'balancing_down_price':100.})
    started=time.perf_counter()
    result=market.run(time_index=index,reserve_requirements=reserves,
        historical_rtbm_prices=history,must_run_requirement_mw=[180.]*48)
    seconds=time.perf_counter()-started
    if not result.audit['passed']:raise RuntimeError('Daily stage audit failed')
    if not all(row['reconciled'] for row in result.economics['participants'].values()):
        raise RuntimeError('Participant asset/cash reconciliation failed')
    readable=result.to_readable(audience='operator')['simulation_summary']['days'][0]['market_stages']
    with localcontext() as context:
        context.prec=80
        for owner,data in readable['settlement']['participants'].items():
            expected=sum((Decimal(e['cash_eur'])*(int(e['account'].startswith(f'participant:{owner}:'))-
                int(e['counterparty'].startswith(f'participant:{owner}:'))) for e in result.ledger),Decimal(0))
            if Decimal(data['financial_position']['daily_total_cash_eur'])!=expected:
                raise RuntimeError('Readable financial position does not reconcile')
    isp=[r['assets']['battery'] for run in result.isp_runs for r in run['rows']]
    rtbm=[r['assets']['battery'] for r in result.rtbm]
    overlaps={name:max((min(r['up_mwh'],r['down_mwh']) for r in rows),default=0.)
              for name,rows in (('ISP',isp),('RTBM',rtbm))}
    cash={p:data['participant_total_cash_eur'] for p,data in result.economics['participants'].items()}
    row=dict(seconds=seconds,solver_seconds_by_stage=result.diagnostics['solver_wall_seconds_by_stage'],
        audit_passed=result.audit['passed'],audit_checks=len(result.audit['checks']),
        storage_activation_formulation=result.isp_runs[0].get('storage_activation_formulation'),
        additional_activation_binaries_by_ISP_run={r['id']:r.get('storage_activation_binary_count',0) for r in result.isp_runs},
        feasible_without_security_slack=result.manifest['physical_security_feasible'],
        maximum_simultaneous_activation_mwh=overlaps,
        storage_up_mwh=sum(r['up_mwh'] for r in rtbm),storage_down_mwh=sum(r['down_mwh'] for r in rtbm),
        ending_storage_state=result.ending_states['battery'],participant_cash_eur=cash,
        scenario_fingerprint=fingerprint({k:v for k,v in result.inputs.items() if k!='system_observation_mw'}),
        implementation_fingerprint=result.manifest['implementation_fingerprint'],
        stages=dict(must_run_mtus=len(result.inputs['must_run']['intervals']),DAM_mtus=len(result.dam['prices_eur_mwh']),
            ISP_runs=len(result.isp_runs),RTBM_intervals=len(result.rtbm),settlement_meters=len(result.meters)),
        smoothing_fallbacks=result.diagnostics['smoothing_fallback_count'])
    return result,row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--require-exclusive',action='store_true')
    args=parser.parse_args()
    if args.repetitions<1:parser.error('repetitions must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    print('Untimed full-day warmup',flush=True)
    run_case(False)
    report=dict(label=args.label,python=platform.python_version(),
        highs=highspy.Highs().version() if highspy is not None else None,
        scenario_fingerprint_basis='normalized inputs excluding endogenous delivered system_observation_mw',
        methodology='sequential; one untimed full-day warmup; Market.run wall time excludes build/export; solver threads=1',cases={})
    # Alternate cases to reduce time-order bias. No concurrent benchmark work.
    for repeat in range(args.repetitions):
        for name,equal in (('distinct_prices',False),('equal_prices',True)):
            result,row=run_case(equal)
            if args.require_exclusive and any(v>1e-7 for v in row['maximum_simultaneous_activation_mwh'].values()):
                raise RuntimeError('Simultaneous storage activation detected')
            case=report['cases'].setdefault(name,dict(runs=[]))
            case['runs'].append(row)
            case['median_seconds']=statistics.median(r['seconds'] for r in case['runs'])
            case['median_solver_seconds_by_stage']={s:statistics.median(r['solver_seconds_by_stage'].get(s,0) for r in case['runs'])
                for s in row['solver_seconds_by_stage']}
            if repeat==0:
                result.save(args.output/name/'raw');result.save_readable(args.output/name)
            (args.output/'benchmark.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
            print(f'{args.label} {name} {repeat+1}: {row["seconds"]:.3f}s',flush=True)


if __name__=='__main__':main()

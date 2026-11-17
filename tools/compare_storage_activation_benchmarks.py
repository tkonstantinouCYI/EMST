"""Validate paired daily activation benchmarks and save median timing changes."""
import argparse,json,statistics
from decimal import Decimal,localcontext
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--after',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    before=json.loads(args.before.read_text());after=json.loads(args.after.read_text())
    assert before['highs']==after['highs'] and before['python']==after['python']
    assert before['methodology']==after['methodology']
    assert before['cases'].keys()==after['cases'].keys()
    report=dict(passed=True,python=after['python'],highs=after['highs'],methodology=after['methodology'],
        before_file=str(args.before),after_file=str(args.after),cases={})
    for name in before['cases']:
        b=before['cases'][name];a=after['cases'][name]
        assert len(b['runs'])==len(a['runs'])
        assert {r['scenario_fingerprint'] for r in b['runs']}=={r['scenario_fingerprint'] for r in a['runs']}
        assert len({r['implementation_fingerprint'] for r in b['runs']})==1
        assert len({r['implementation_fingerprint'] for r in a['runs']})==1
        assert b['runs'][0]['implementation_fingerprint']!=a['runs'][0]['implementation_fingerprint']
        for row in b['runs']+a['runs']:
            assert row['audit_passed'] and row['feasible_without_security_slack']
            assert row['stages']['must_run_mtus']==row['stages']['DAM_mtus']==48
            assert row['stages']['ISP_runs']==2 and row['stages']['RTBM_intervals']==288
        assert all(max(row['maximum_simultaneous_activation_mwh'].values())<=1e-7 for row in a['runs'])
        with localcontext() as context:
            context.prec=80
            changes={p:str(statistics.median(Decimal(r['participant_cash_eur'][p]) for r in a['runs'])-
                statistics.median(Decimal(r['participant_cash_eur'][p]) for r in b['runs'])) for p in a['runs'][0]['participant_cash_eur']}
        report['cases'][name]=dict(repetitions=len(a['runs']),before_median_seconds=b['median_seconds'],
            after_median_seconds=a['median_seconds'],change_percent=(a['median_seconds']/b['median_seconds']-1)*100,
            before_solver_median_seconds_by_stage=b['median_solver_seconds_by_stage'],
            after_solver_median_seconds_by_stage=a['median_solver_seconds_by_stage'],
            before_maximum_simultaneous_mwh={s:max(r['maximum_simultaneous_activation_mwh'][s] for r in b['runs']) for s in ('ISP','RTBM')},
            after_maximum_simultaneous_mwh={s:max(r['maximum_simultaneous_activation_mwh'][s] for r in a['runs']) for s in ('ISP','RTBM')},
            participant_cash_change_eur=changes,all_stages_verified=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    for name,row in report['cases'].items():
        print(f"{name}: {row['before_median_seconds']:.3f}s -> {row['after_median_seconds']:.3f}s ({row['change_percent']:+.1f}%)")


if __name__=='__main__':main()

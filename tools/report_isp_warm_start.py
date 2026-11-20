"""Render measured cold/warm daily comparisons, including preparation cost."""
import argparse,json
from decimal import Decimal
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--benchmarks',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    summaries=[json.loads((v/'summary.json').read_text()) for v in args.benchmarks]
    assert all(v['passed'] for v in summaries)
    groups=[v for s in summaries for v in s['groups']];checks=[v for s in summaries for v in s['comparisons']]
    first=json.loads((args.benchmarks[0]/'runs.json').read_text())[0]
    lines=['# ISP warm-start benchmarks','',
        f"Python {first['python']}, HiGHS {first['solver_version']}, one thread, fresh sequential workers. Three measured repetitions per case/resolution/mode, alternating order. Every primary MILP retains a 90-second limit and zero absolute/relative optimality gaps. Warm runs add a five-second repair budget; total elapsed times include repair, candidate submission and all market stages.",
        '', 'All inputs are public synthetic examples. Storage includes RES, forwards, DAM storage blocks, must-run procurement, balancing energy/capacity and daily settlement. Configured uses the compact combined-cycle example. Synthetic uses the more demanding compact configured-fleet small case with two conventional units and directional reserve requirements. The latter is a separately labelled slack-aware diagnostic: its optimum uses 0.01749277 MWh existing energy-shortage slack in MTU27 of each ISP run, economically preferred to starting another unit. No physical constraints, input data or slack penalties were changed to remove that shortage.',
        '', '`boundary` generates its initial hint from boundary states. `reused` receives a pre-existing identical-forecast initial ISP solution. Both warm modes use the current initial ISP remainder for the noon run. Historical seed generation is timed and saved separately in each summary; it is not free if the user has to solve a new case solely to create that seed. Reused-mode results do not establish performance under changed forecasts.',
        '', 'ISP total is the complete dispatch call, including construction, repair, final MILP and fixed-discrete pricing LP. Repair includes hint/model-copy preparation and its LP/MILP attempts. Columns are independent repeat medians and need not add exactly. Day time includes every stage and result enrichment, excluding file export and process startup.',
        '', '| Case | RTBM min | Mode | ISP1 repair s | ISP1 final MILP s | ISP1 total s | ISP2 repair s | ISP2 final MILP s | ISP2 total s | Day s |',
        '|---|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in groups:
        a=g['median_repair_seconds'];b=g['median_primary_MILP_seconds'];c=g['median_ISP_dispatch_seconds']
        lines.append(f"| {g['case']} | {g['minutes']} | {g['mode']} | {a[0]:.3f} | {b[0]:.3f} | {c[0]:.3f} | {a[1]:.3f} | {b[1]:.3f} | {c[1]:.3f} | {g['median_day_seconds']:.3f} |")
    objective=max(v for c in checks for v in c['ISP_objective_differences'])
    power=max(c['maximum_dispatch_difference_mw'] for c in checks)
    price=max(c['maximum_RTBM_price_difference_eur_mwh'] for c in checks)
    settlement=max(c['maximum_settlement_price_difference_eur_mwh'] for c in checks)
    cash=max(abs(Decimal(v)) for c in checks for v in c['participant_cash_difference_eur'].values())
    lines+=['','## Correctness','',
        f'Every daily run passed all stage audits and reconciled participant cashflows. Storage and configured examples used no security slack; the additional synthetic diagnostic retains its explicitly reported existing ISP shortage. It is not a security-feasible operating-day claim. Model dimensions and certified ISP objectives match across cold/warm pairs. Across {len(checks)} paired comparisons, maximum differences were: ISP objective EUR{objective:.9g}; RTBM dispatch {power:.9g} MW; RTBM price EUR{price:.9g}/MWh; settlement price EUR{settlement:.9g}/MWh; participant daily cash EUR{cash:.9g}.',
        '', 'The final economic model is not restricted by the start. Dedicated tests verify a final MILP can improve the repaired commitment, correction of an illegal initial state, uninterrupted inherited transitions with actual entry power, storage SOC-target repair, budget-exhausted cold fallback, both configuration formulations and native HiGHS/CBC start submission. Final time-limited market incumbents remain inadmissible to settlement.',
        '', 'Warm-started two-day continuous/checkpoint-resumed results matched completely except clocks at both RTBM resolutions, including economics, component histories, an unfinished midnight transition and once-only transition costs. Evidence: `output/isp-warm-resume-final-5/verification.json` and `output/isp-warm-resume-final-30/verification.json`. The final focused warm-start tests also cover a zero-price constant objective, where PuLP adds an auxiliary column during solve.',
        '', 'Regression evidence covers 178 cases: the initial full run passed 177, with one checkpoint comparison crossing a live source-code update and comparing different implementation fingerprints. That test passed in both RTBM modes on a frozen-source rerun. The original full-run result is retained alongside `output/isp-warm-start-tests/frozen-checkpoint-rerun.json`; it has not been rewritten as a single all-green run. The final seven focused warm-start methods also passed.',
        '', 'Warm starts are disabled by default. Compare complete ISP/day time: candidate preparation can outweigh reductions in the final MILP. These small daily cases do not establish that warm starts solve the large-fleet HiGHS presolve bottleneck.',
        '', '## Reproduction and evidence','', '```sh',
        'python tools/benchmark_isp_warm_start.py --repetitions 3 --output output/isp-warm-start-benchmarks',
        'python tools/benchmark_isp_warm_start.py --cases synthetic --repetitions 3 --output output/isp-warm-start-synthetic-slack-diagnostic',
        'python tools/run_tests.py --output output/isp-warm-start-tests', '```','',
        'Each worker saves raw/readable results (including financial positions), seed JSON, timings, full warm-start diagnostics and a worker log. Each panel saves all individual runs, paired comparisons, seed-generation costs and medians. Evidence directories: '+', '.join('`'+v.as_posix()+'`' for v in args.benchmarks)+'.',
        '', 'See [configuration and repair details](isp-warm-starts.md).']
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':main()

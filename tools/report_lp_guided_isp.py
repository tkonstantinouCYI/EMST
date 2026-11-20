"""Report the matched ten-unit LP-guided versus cold/previous-ISP comparison."""
import argparse,json,statistics
from decimal import Decimal
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--benchmark',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    summary=json.loads((args.benchmark/'summary.json').read_text());runs=json.loads((args.benchmark/'runs.json').read_text());assert summary['passed']
    first=runs[0]
    lines=['# Ten-thermal LP-guided warm-start comparison','',
        f"Synthetic 48-half-hour ISP, ten conventional thermal units, demand/RES and directional FCR/aFRR/mFRR reserves. Python {first['python']}, HiGHS {first['solver_version']}, one thread, fresh sequential worker processes, three repetitions and alternating solve order. Every final MILP retains the same 90-second limit and zero absolute/relative gaps.",
        '', 'Inputs exactly reuse `tools/benchmark_relaxed_isp.py`; no ramp, transition, minimum-time, reserve or other physical constraint is relaxed in final clearing. Initial/noon final-MILP dimensions are identical across modes. Each LP-guided preparation shares a five-second requested budget across guiding LP and repair. Overhead and native-limit overshoot are included in complete elapsed time.',
        '', 'Previous-ISP mode uses a pre-existing identical-forecast initial ISP result; its historical seed-generation cost is retained separately in summary JSON. The noon schedule seed comes from that run\'s own initial ISP. This is an ideal previous-schedule test, not a learned prediction. LP mode solves the current relaxation at both ISP runs.',
        '', '## Median timings','', 'Preparation includes LP guidance, copying and feasibility repair; guiding LP is a subset, not an additional amount to add to preparation. ISP total includes construction, preparation, final MILP and fixed-discrete pricing. Independent medians need not sum exactly.']
    for index in (0,1):
        lines+=['',f'### ISP {index+1}'+(' — initial, 48 MTUs' if index==0 else ' — noon, 24 MTUs'),'',
            '| RTBM min | Start | Guiding LP s | All preparation s | Final MILP s | ISP total s | Submitted starts / attempts |',
            '|---:|---|---:|---:|---:|---:|---:|']
        for g in summary['groups']:
            selected=[r for r in runs if r['minutes']==g['minutes'] and r['mode']==g['mode']]
            attempts=[r['warm_start'][index] for r in selected if r['warm_start'][index] is not None]
            submitted=sum(w['candidate_available'] and w['submission_status']=='HighsStatus.kOk' for w in attempts)
            lines.append(f"| {g['minutes']} | {g['mode']} | {g['median_LP_guidance_seconds'][index]:.3f} | {g['median_repair_seconds'][index]:.3f} | {g['median_primary_MILP_seconds'][index]:.3f} | {g['median_ISP_dispatch_seconds'][index]:.3f} | {submitted}/{len(attempts)} |")
    lines+=['','### Complete day','', '| RTBM min | Start | Day s |','|---:|---|---:|']
    for g in summary['groups']:lines.append(f"| {g['minutes']} | {g['mode']} | {g['median_day_seconds']:.3f} |")
    lines+=['','### Paired differences','',
        'Median of each warm-minus-cold difference within the same repetition; positive means slower. Day timings vary materially across repeats. Comparing independently computed day medians can reverse the apparent direction, so use these paired differences alongside the individual run data.',
        '', '| RTBM min | Warm source | Initial ISP delta s | Noon ISP delta s | Complete day delta s |','|---:|---|---:|---:|---:|']
    for minutes in sorted({r['minutes'] for r in runs}):
        for mode in ('lp','reused'):
            warm=[r for r in runs if r['minutes']==minutes and r['mode']==mode]
            pairs=[(r,next(c for c in runs if c['minutes']==minutes and c['mode']=='cold' and c['repeat']==r['repeat'])) for r in warm]
            delta=[statistics.median(a['ISP_dispatch_wall_seconds'][i]-b['ISP_dispatch_wall_seconds'][i] for a,b in pairs) for i in (0,1)]
            day=statistics.median(a['total_day_wall_seconds']-b['total_day_wall_seconds'] for a,b in pairs)
            lines.append(f'| {minutes} | {mode} | {delta[0]:.3f} | {delta[1]:.3f} | {day:.3f} |')
    checks=summary['comparisons'];obj=max(v for c in checks for v in c['ISP_objective_differences'])
    cash=max(abs(Decimal(v)) for c in checks for v in c['participant_cash_difference_eur'].values())
    lines+=['','## Validation and limitations','',
        f"All {len(runs)} measured daily runs passed stage audits and participant cash reconciliation. Across {len(checks)} paired comparisons, maximum ISP objective difference was EUR{obj:.9g}; dispatch difference {max(c['maximum_dispatch_difference_mw'] for c in checks):.9g} MW; RTBM price difference EUR{max(c['maximum_RTBM_price_difference_eur_mwh'] for c in checks):.9g}/MWh; settlement price difference EUR{max(c['maximum_settlement_price_difference_eur_mwh'] for c in checks):.9g}/MWh; participant cash difference EUR{cash:.9g}.",
        '', 'Nineteen targeted regression/integration methods passed (prior warm-start controls/repair, LP-guided repair, configured/storage daily replay in both RTBM modes and solver/planning controls). CBC LP-guided repair also passed a smoke test. Physical input fingerprints match across modes after excluding solver options; evidence is in `output/lp-guided-10-thermal-verification.json` and `output/lp-guided-*.log`. A new full-library regression run is not claimed for this change.',
        '', 'The guiding LP has fractional commitment values. Rounding is only a suggestion: repair enforces the full original model before a candidate can be submitted. A repair MILP may reach its time limit with a validated feasible incumbent; that is allowed only as a starting point. Final clearing must still meet its original optimal-termination policy. LP or repair prices are never used in settlement.',
        '', 'The same ten-unit inputs are retained even when fine-resolution delivery needs the existing security slack. This is reported below, not hidden or removed by changing penalties. ISP energy slack and RTBM delivery slack have different meanings. These rows describe the first cold repeat for each resolution; all individual rows remain in metrics/raw files.',
        '', '| RTBM min | Initial ISP shortage MWh | Noon ISP shortage MWh | RTBM shortage MWh | RTBM surplus MWh |','|---:|---:|---:|---:|---:|']
    for minutes in sorted({r['minutes'] for r in runs}):
        r=next(r for r in runs if r['minutes']==minutes and r['mode']=='cold')
        lines.append(f"| {minutes} | {r['ISP_security_slack'][0]['energy_shortage_mwh']:.9g} | {r['ISP_security_slack'][1]['energy_shortage_mwh']:.9g} | {r['RTBM_security_slack']['energy_shortage_mwh']:.9g} | {r['RTBM_security_slack']['energy_surplus_mwh']:.9g} |")
    lines+=['', 'All modes retain the full unrestricted MILP. Smaller LP time does not imply a faster feasible integer solution: repair itself can require another substantial integer solve. Warm starts remain disabled by default; no universal speedup is inferred.',
        '', '## Replay','', '```sh',
        'python tools/benchmark_isp_warm_start.py --cases thermal10 --modes cold lp reused --repetitions 3 --output output/lp-guided-10-thermal-comparison',
        'python -m unittest discover -s tests -p test_lp_warm_start.py -v','```','',
        f'Every input, raw/readable result, financial position, seed, solver option and timing is retained under `{args.benchmark.as_posix()}`. See [LP-guided configuration and repair](lp-guided-warm-starts.md).']
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':main()

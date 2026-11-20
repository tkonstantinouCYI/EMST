"""Before/after transition-capacity benchmark for the same one-CC hourly ISP."""
import json
import statistics
from datetime import datetime
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.benchmark_hourly_virtual_cc import run


def main():
    out=ROOT/'output/handoff-isp'/('cc-transition-reserves-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,exist_ok=False)
    natural=[]
    for repeat in range(3):
        for enabled in ((False,True) if repeat%2==0 else (True,False)):
            natural.append(run(out/f'natural-{repeat}-enabled-{int(enabled)}',True,transition_reserves=enabled))
    forced=[run(out/f'forced-enabled-{int(enabled)}',True,forced=True,transition_reserves=enabled) for enabled in (False,True)]
    assert all(r['solve']['proven_optimal'] for r in natural+forced)
    assert len({r['variables'] for r in natural+forced})==1
    assert len({r['binaries'] for r in natural+forced})==1
    assert sum(forced[1]['solve']['cc_transition_reserve_mw_hours'].values())>0
    assert forced[1]['solve']['objective']<=forced[0]['solve']['objective']+1e-4
    summary={str(enabled):dict(median_construction_seconds=statistics.median(r['construction_seconds'] for r in natural if r['transition_reserves']==enabled),
        median_solve_seconds=statistics.median(r['solve']['solve_wall_seconds'] for r in natural if r['transition_reserves']==enabled),
        median_total_seconds=statistics.median(r['total_seconds'] for r in natural if r['transition_reserves']==enabled)) for enabled in (False,True)}
    (out/'comparison.json').write_text(json.dumps(dict(natural=natural,forced=forced,summary=summary),indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(out),summary=summary)),flush=True)


if __name__=='__main__':main()

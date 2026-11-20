"""Warm-started two-day commitment and checkpoint replay across midnight."""
import argparse,json,sys
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from examples.configured_plant import build_market
from emst.solver import configuration


def strip_clocks(v):
    if isinstance(v,dict):return {k:strip_clocks(x) for k,x in v.items() if k not in ('wall_seconds','solver_wall_seconds_by_stage','preparation_wall_seconds','repair_solver_wall_seconds')}
    if isinstance(v,list):return [strip_clocks(x) for x in v]
    return v


def main():
    p=argparse.ArgumentParser();p.add_argument('--minutes',type=int,choices=(5,30),default=30);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    m=build_market(configuration_formulation='compact');m.solver_config=configuration({'stages':{'ISP':{'warm_start':True}}})
    e=next(e for e in m.assets['ccgt'].parameters['plant_configurations']['transitions'] if e['id']=='add2')
    e['profile_mw'].append(140.);e['components_by_step'].append(['GT1','GT2','ST'])
    history={t:[[60.]*6 for _ in range(3)] for t in range(48)}
    scenarios={d:dict(forecasts={'load':load},actuals={'load':load},historical_rtbm_prices=history,rtbm_interval_minutes=args.minutes) for d,load in
        (('2026-11-05',[100.]*47+[120.]),('2026-11-06',[170.]*48))}
    full=m.run_period(start='2026-11-05',end='2026-11-06',scenario=scenarios,checkpoint_directory=args.output/'full')
    first=m.run_period(start='2026-11-05',end='2026-11-05',scenario=scenarios,checkpoint_directory=args.output/'split')
    ending=first.days['2026-11-05'].ending_states['ccgt'];assert ending['transition']=={'id':'add2','elapsed_minutes':60}
    resumed=m.run_period(start='2026-11-06',end='2026-11-06',scenario=scenarios,checkpoint_directory=args.output/'split',resume_from=first.checkpoint)
    for day,r in full.days.items():
        assert strip_clocks(json.loads(json.dumps(asdict(r))))==strip_clocks(json.loads(json.dumps(asdict(resumed.days[day])))),day
        assert r.audit['passed'] and r.manifest['physical_security_feasible']
        assert all(v['reconciled'] for v in r.economics['participants'].values())
        assert all(x['warm_start']['candidate_available'] and x['warm_start']['submission_status']=='HighsStatus.kOk' for x in r.isp_runs)
        r.save_readable(args.output/day)
    second=full.days['2026-11-06'];assert second.rtbm[0]['assets']['ccgt']['configuration_state']=='add2:2'
    assert sum(v['assets']['ccgt']['transition_cost_eur'] for v in second.rtbm)==0
    report=dict(passed=True,RTBM_minutes=args.minutes,complete_results_match_excluding_clocks=True,
        both_ISP_runs_warm_started_each_day=True,all_stages_passed=True,participant_cash_reconciled=True,
        no_security_slack=True,unfinished_midnight_transition_preserved=True,transition_cost_not_repeated=True)
    (args.output/'verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()

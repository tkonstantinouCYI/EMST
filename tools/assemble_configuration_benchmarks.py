"""Assemble validated primary HiGHS and CPLEX panels; preserve diagnostic runs."""
import argparse,json,statistics
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--highs',type=Path,required=True);p.add_argument('--cplex',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    runs=[r for r in json.loads((args.highs/'runs.json').read_text()) if r['solver']=='highs']+[r for r in json.loads((args.cplex/'runs.json').read_text()) if r['solver']=='cplex']
    args.output.mkdir(parents=True,exist_ok=True);(args.output/'runs.json').write_text(json.dumps(runs,indent=2))
    groups={};models={}
    for r in runs:
        groups.setdefault((r['case'],r['solver'],r['formulation']),[]).append(r)
        models.setdefault((r['case'],r['formulation']),set()).add(r['model_lp_sha256'])
        if r.get('objective') is not None:
            assert r['maximum_primal_violation']<1e-5,(r['directory'],r['maximum_primal_violation'])
            assert r['integer_violation']<1e-6,(r['directory'],r['integer_violation'])
            if r.get('configuration_history_checks'):assert all(r['configuration_history_checks'].values())
    assert all(len(v)==1 for v in models.values()),models
    comparisons={};summary=[]
    for (case,solver,variant),g in groups.items():
        summary.append(dict(case=case,solver=solver,formulation=variant,repetitions=len(g),before_presolve=g[0]['before_presolve'],
            median_construction_seconds=statistics.median(r['construction_seconds'] for r in g),median_solver_seconds=statistics.median(r['total_solver_seconds'] for r in g),
            statuses=[r['status'] for r in g],objectives=[r.get('objective') for r in g],bounds=[r.get('best_bound') for r in g],gaps=[r.get('gap') for r in g]))
        key=case+'-'+solver
        if key in comparisons:continue
        pairs=[]
        for a,b in zip(groups[case,solver,'reference'],groups[case,solver,'compact']):
            assert a['input_sha256']==b['input_sha256'] and a['settings']==b['settings']
            certified=a.get('gap')==b.get('gap')==0 and a.get('objective') is not None and b.get('objective') is not None
            diff=abs(a['objective']-b['objective']) if certified else None
            if certified:assert diff<.001,(key,diff)
            root=b['root_lp']['objective']-a['root_lp']['objective'] if a['root_lp'].get('objective') is not None and b['root_lp'].get('objective') is not None else None
            if root is not None:assert root>-.001,(key,'root relaxation',root)
            mismatches=sum(x!=y for aid,sequence in a.get('state_schedules',{}).items() for x,y in zip(sequence,b.get('state_schedules',{}).get(aid,[]))) if a.get('state_schedules') and b.get('state_schedules') else None
            pairs.append(dict(both_certified_optimal=certified,objective_difference=diff,compact_minus_reference_LP_bound=root,state_mismatches=mismatches))
        comparisons[key]=pairs
    (args.output/'summary.json').write_text(json.dumps(dict(passed=True,groups=summary,comparisons=comparisons,
        provenance=dict(highs=str(args.highs),cplex=str(args.cplex),cplex_repeat_presolve=0,
            excluded='Default-tolerance failures and repeat-presolve-sensitive inconsistent CPLEX certificates retained in diagnostic directories; no physical model change.')),indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()

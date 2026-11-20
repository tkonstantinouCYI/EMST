"""Untimed physical-point lifting between matched exported ISP formulations."""
import argparse,json,sys,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.benchmark_configurations import capture,lp_value
from emst.optimization import solve,primal_violation
from emst.solver import session
import pulp as lp


def main():
    p=argparse.ArgumentParser();p.add_argument('--benchmark',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    runs=json.loads((args.benchmark/'runs.json').read_text());checks=[]
    grouped={}
    for r in runs:
        if r.get('objective') is not None:grouped.setdefault((r['case'],r['solver'],r['formulation']),r)
    for (case,solver,source),run in grouped.items():
        target='compact' if source=='reference' else 'reference';directory=Path(run['directory']);data=json.loads((directory/'inputs.json').read_text());values=json.loads((directory/'solution.json').read_text())
        src=capture(data,source)['problem'];dst=capture(data,target)['problem']
        for v in src.variables():v.varValue=values.get(v.name,0.)
        original_constraints=list(dst.constraints.values())
        common=0
        for v in dst.variables():
            if v.name in values:
                point=values[v.name]
                if v.isInteger():v.lowBound=v.upBound=round(point)
                else:
                    v.lowBound=max(v.lowBound if v.lowBound is not None else -float('inf'),point-1e-6)
                    v.upBound=min(v.upBound if v.upBound is not None else float('inf'),point+1e-6)
                common+=1
            v.cat=lp.LpContinuous
        for aid,z in dst._emst_configuration_states.items():
            for key,expr in z.items():dst+=expr==round(lp_value(src._emst_configuration_states[aid][key]))
        dst+=dst.objective<=run['objective']+.001;dst+=dst.objective>=run['objective']-.001
        with session(dict(backend='highs',threads=1,time_limit_seconds=120,relative_gap=0.,absolute_gap=0.)):
            objective=solve(dst,False)
        violation=primal_violation(dst.variables(),original_constraints)
        assert violation<1e-5 and abs(objective-run['objective'])<=.0011
        checks.append(dict(case=case,solver=solver,source=source,target=target,source_solver_claimed_optimal=run.get('gap')==0,
            source_objective=run['objective'],lifted_objective=objective,maximum_original_constraint_violation=violation,
            common_variables_pinned=common,physical_point_radius=1e-6,objective_numeric_band_eur=.001,
            original_physical_constraints_unchanged=True,passed=True))
        print(case,solver,source,'->',target,'passed',flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(dict(passed=True,checks=checks),indent=2))

if __name__=='__main__':main()

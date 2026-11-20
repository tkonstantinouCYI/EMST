"""Forced multi-plant end-to-end equivalence, including 13-step noon crossing."""
import argparse,json,sys,copy
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from examples.synthetic_configured_fleet import synthetic_case,build_market
from emst.calendar import CYPRUS
from emst.solver import details,stage_name
import emst.dispatch as dispatch_module


def plans(data):
    result={}
    def edge(p,source,target):return next(e for e in p['transitions'] if e['source']==source and e['target']==target)
    def trajectory(e,offset=0):return [f"{e['id']}:{k}" for k in range(offset,len(e['profile_mw']))]
    p=data['plants'][0];e=edge(p,'OFF','GT1_ST');result[p['id']]=['OFF']*12+trajectory(e)+['GT1_ST']*23
    p=data['plants'][1];e=edge(p,'OFF','GT1_ST');result[p['id']]=trajectory(e,6)+['GT1_ST']*41
    p=data['plants'][2];e=edge(p,'GT1_ST','GT1_GT2_ST');f=edge(p,'GT1_GT2_ST','GT1_GT2_GT3_ST');result[p['id']]=['GT1_ST']*8+trajectory(e)+trajectory(f)+['GT1_GT2_GT3_ST']*27
    assert all(len(v)==48 for v in result.values())
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    data=synthetic_case('long');data['name']='forced_daily_fleet';data['demand_mw']=[v+200 for v in data['demand_mw']]
    for c in data['conventional']:c['initial_state']['power_mw']=100.
    schedule=plans(data);data['forced_configuration_states']=schedule;data['force_conventional_dispatch']=True
    (args.output/'inputs.json').write_text(json.dumps(data,indent=2));report=dict(passed=True,modes={})
    original=dispatch_module.solve
    def forced(problem,mip=True):
        if mip and stage_name(problem)=='ISP':
            at=datetime.fromisoformat(details()['horizon_start']).astimezone(CYPRUS);start=at.hour*2+at.minute//30;n=details()['horizon_intervals']
            for aid,z in problem._emst_configuration_states.items():
                for j in range(n):problem+=z[schedule[aid][start+j],j]==1
            variables={v.name:v for v in problem.variables()}
            for i in range(len(data['plants']),len(data['plants'])+len(data['conventional'])):
                for j in range(n):problem+=variables[f'thermal_{i}_dispatch_{j}']==1
        return original(problem,mip)
    for minutes in (5,30):
        results={}
        for variant in ('reference','compact'):
            m=build_market(data,variant)
            with patch.object(dispatch_module,'solve',side_effect=forced):
                r=m.run(time_index=m.time_index(data['day']),rtbm_interval_minutes=minutes,
                    reserve_requirements=data['reserve_requirements'],historical_rtbm_prices={int(k):v for k,v in data['historical_rtbm_prices'].items()})
            assert r.audit['passed'] and r.manifest['physical_security_feasible']
            assert all(v['reconciled'] for v in r.economics['participants'].values())
            cost=sum(v['assets'][a]['transition_cost_eur'] for v in r.rtbm for a in schedule)
            assert cost==11000,cost
            assert r.isp_runs[1]['forecast_noon_state']['ccgt_0']['transition']=={'id':'e0','elapsed_minutes':360}
            for aid,expected in schedule.items():assert [v['assets'][aid]['configuration_state'] for v in r.rtbm]==[s for s in expected for _ in range(30//minutes)]
            expected_clocks={'ccgt_0':{'GT1':1080,'ST':1080},'ccgt_1':{'GT1':1620,'ST':1620},'ccgt_2':{'GT1':2040,'GT2':1200,'GT3':990,'ST':2040}}
            for aid,clocks in expected_clocks.items():
                for c,duration in clocks.items():assert r.ending_states[aid]['component_histories'][c]['duration_minutes']==duration,(aid,c,r.ending_states[aid])
            folder=args.output/f'{variant}-{minutes}';r.save(folder/'raw');r.save_readable(folder);results[variant]=r
            print(variant,minutes,'all stages passed; 13-step noon transition and three-GT back-to-back path; cost',cost,flush=True)
        a,b=results['reference'],results['compact']
        objectives=[abs(x['milp_objective_eur']-y['milp_objective_eur']) for x,y in zip(a.isp_runs,b.isp_runs)]
        assert max(objectives)<1e-3,objectives
        report['modes'][str(minutes)]=dict(all_stage_checks_passed=True,all_cash_reconciled=True,security_slack_used=False,
            ISP_objective_differences=objectives,executed_transition_cost_eur=11000,
            dispatch_max_difference_mw=max(abs(x['assets'][aid]['power_mw']-y['assets'][aid]['power_mw']) for x,y in zip(a.rtbm,b.rtbm) for aid in schedule),
            noon_inherited_transition=True,three_GT_back_to_back=True,component_clocks_preserved=True,
            note='Configuration and conventional commitment paths are explicitly forced in both variants; physical power/ramp/reserve constraints remain enforced. Reserve allocation/dual ties may differ.')
    (args.output/'verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':main()

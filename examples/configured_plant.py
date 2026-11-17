"""One combined-cycle market entity with mutually exclusive virtual configurations."""
import argparse,json
from pathlib import Path
from emst import Market,PlantState,ThermalState


def build_market(demand=None):
    demand=[60.,80.,110.]+[130.]*45 if demand is None else demand
    m=Market();m.add_participant('plant_owner');m.add_participant('supplier')
    components={name:dict(minimum_up_minutes=60,minimum_down_minutes=60) for name in ('GT1','GT2','ST')}
    def cfg(members,minimum,maximum,cost):
        return dict(components=members,minimum_mw=minimum,maximum_mw=maximum,marginal_cost=cost,
            ramp_mw_hour=400,reserve_limit_mw=20,no_load_cost_per_hour=0)
    configs={'OFF':cfg([],0,0,0),'GT1_ST':cfg(['GT1','ST'],20,80,60),
        'GT2_ST':cfg(['GT2','ST'],20,80,65),'GT1_GT2_ST':cfg(['GT1','GT2','ST'],60,160,55)}
    configs['OFF']['reserve_limit_mw']=0
    transitions=[]
    def edge(name,source,target,profile,status,cost):
        transitions.append(dict(id=name,source=source,target=target,profile_mw=profile,components_by_step=status,cost_eur=cost,ramp_mw_hour=400))
    edge('start1','OFF','GT1_ST',[20,60],[['GT1','ST']]*2,1000)
    edge('start2','OFF','GT2_ST',[20,60],[['GT2','ST']]*2,1000)
    edge('add2','GT1_ST','GT1_GT2_ST',[100,120],[['GT1','GT2','ST']]*2,250)
    edge('add1','GT2_ST','GT1_GT2_ST',[100,120],[['GT1','GT2','ST']]*2,250)
    edge('drop2','GT1_GT2_ST','GT1_ST',[80,60],[['GT1','GT2','ST'],['GT1','ST']],100)
    edge('drop1','GT1_GT2_ST','GT2_ST',[80,60],[['GT1','GT2','ST'],['GT2','ST']],100)
    edge('stop1','GT1_ST','OFF',[20,0],[['GT1','ST'],[]],50)
    edge('stop2','GT2_ST','OFF',[20,0],[['GT2','ST'],[]],50)
    m.add_configured_thermal('ccgt','plant_owner',components=components,configurations=configs,transitions=transitions,
        initial_state=PlantState('GT1_ST',60,{c:dict(on=c!='GT2',duration_minutes=600) for c in components}))
    # A distinct flexible unit balances within-MTU transition trajectories.
    m.add_participant('backup_owner')
    m.add_thermal('backup','backup_owner',capacity_mw=100,minimum_mw=0,marginal_cost=200,
        ramp_mw_hour=2000,initial_state=ThermalState('dispatch',40,600),minimum_up_minutes=0,minimum_down_minutes=0)
    demand=[v+40 for v in demand]
    m.add_supplier('load','supplier',demand_forecast=demand,actual_consumption=demand)
    return m


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtbm-interval-minutes',type=int,choices=(5,30),default=5)
    parser.add_argument('--output',type=Path,default=Path('configuration-results'))
    args=parser.parse_args();m=build_market()
    r=m.run(time_index=m.time_index('2026-11-05'),rtbm_interval_minutes=args.rtbm_interval_minutes,
        historical_rtbm_prices={i:[[60.]*6]*3 for i in range(48)})
    assert r.audit['passed'] and r.manifest['physical_security_feasible']
    assert all(v['reconciled'] for v in r.economics['participants'].values())
    r.save(args.output/'raw');r.save_readable(args.output)
    summary=dict(passed=True,rtbm_interval_minutes=args.rtbm_interval_minutes,
        ISP_transition_cost_eur=[run['objective_components_eur']['configuration_transition_cost'] for run in r.isp_runs],
        executed_transition_cost_eur=sum(row['assets']['ccgt']['transition_cost_eur'] for row in r.rtbm),
        ending_state=r.ending_states['ccgt'],audit=r.audit)
    (args.output/'verification.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()

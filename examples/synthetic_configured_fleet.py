"""Public, deterministic synthetic ISP fleets; no Cyprus/private data inputs."""
from copy import deepcopy
import argparse,json,math
from pathlib import Path
from itertools import combinations
from emst import Market,PlantState,ThermalState


def plant_spec(i,gas_turbines=2,long=True,initial='single'):
    gts=[f'GT{k+1}' for k in range(gas_turbines)];components={c:dict(minimum_up_minutes=120,minimum_down_minutes=90) for c in gts+['ST']}
    components['ST']=dict(minimum_up_minutes=180,minimum_down_minutes=120)
    configs={'OFF':dict(components=[],minimum_mw=0,maximum_mw=0,marginal_cost=0,ramp_mw_hour=500,reserve_limit_mw=0,no_load_cost_per_hour=0)}
    members={'OFF':()}
    for count in range(1,gas_turbines+1):
        for group in combinations(gts,count):
            name='_'.join(group)+'_ST';members[name]=group
            configs[name]=dict(components=list(group)+['ST'],minimum_mw=60*count,maximum_mw=180*count,
                marginal_cost={1:70,2:50,3:48}[count]+i*.25,ramp_mw_hour=500,reserve_limit_mw=30*count,no_load_cost_per_hour=10*count)
    edges=[]
    for source,a in members.items():
        for target,b in members.items():
            if len(set(a)^set(b))!=1:continue
            rising=len(b)>len(a)
            if not a:length=13 if long else 2;entry=0.;finish=120.
            elif not b:length=3 if long else 2;entry=120.;finish=0.
            elif rising:length=7 if len(b)==2 and long else 6 if long else 2;entry=120.*len(a);finish=120.*len(b)
            else:length=5 if len(a)==2 and long else 4 if long else 2;entry=120.*len(a);finish=120.*len(b)
            profile=[entry+(finish-entry)*(k+1)/length for k in range(length)]
            # A three-GT fleet uses 6/4 steps for pair/triple transitions; lengths
            # total 150 steps, yielding 166 reference binaries/MTU incl events.
            status=[list(set(a)|set(b))+(['ST'] if a or b else []) for _ in range(length)]
            status=[sorted(v) for v in status];status[-1]=list(b)+(['ST'] if b else [])
            edges.append(dict(id=f'e{len(edges)}',source=source,target=target,profile_mw=profile,components_by_step=status,
                cost_eur=8000 if not a else 1500 if rising else 300,ramp_mw_hour=500))
    first=gts[0]+'_ST';both='_'.join(gts)+'_ST'
    configuration='OFF' if initial in ('off','inherited') else both if initial=='all' else first
    online=set(configs[configuration]['components']);power=120*len(members[configuration]);transition=None
    histories={c:dict(on=c in online,duration_minutes=600) for c in components}
    if initial=='inherited':
        e=next(e for e in edges if e['source']=='OFF' and e['target']==first)
        elapsed=180;power=e['profile_mw'][5];transition=dict(id=e['id'],elapsed_minutes=elapsed)
        histories={c:dict(on=c in e['components_by_step'][5],duration_minutes=elapsed if c in e['components_by_step'][5] else 780) for c in components}
    return dict(id=f'ccgt_{i}',participant=f'owner_{i}',components=components,configurations=configs,transitions=edges,
        initial_state=dict(configuration=configuration,power_mw=power,component_histories=histories,transition=transition))


def synthetic_case(name='small'):
    if name not in ('small','long','fleet','forced_long'):raise ValueError('Unknown synthetic case')
    if name=='small':plants=[plant_spec(0,long=False)];normal_count=2
    elif name in ('long','forced_long'):plants=[plant_spec(0,initial='off'),plant_spec(1,initial='inherited'),plant_spec(2,3,initial='single')];normal_count=4
    else:plants=[plant_spec(i,initial=['single','off','inherited','all'][i]) for i in range(4)]+[plant_spec(4,3,initial='off')];normal_count=8
    scale=1 if name=='small' else 2 if name in ('long','forced_long') else 3
    demand=[scale*(390+90*math.sin(2*math.pi*(t-13)/48)+45*math.sin(4*math.pi*(t-5)/48)) for t in range(48)]
    res=[scale*(40+15*math.sin(2*math.pi*(t+2)/48)+85*max(0,math.sin(math.pi*(t-12)/24))) for t in range(48)]
    normals=[dict(id=f'thermal_{i}',participant=f'conventional_{i}',capacity_mw=250,minimum_mw=20,marginal_cost=100+10*i,
        ramp_mw_hour=1200,minimum_up_minutes=60,minimum_down_minutes=60,startup_cost=500,
        initial_state=dict(stage='dispatch',power_mw=60,time_in_stage_minutes=600)) for i in range(normal_count)]
    return dict(schema=1,name=name,day='2026-11-05',plants=plants,conventional=normals,demand_mw=demand,res_mw=res,
        reserve_requirements={'FCR':{'up':[2.*scale]*48,'down':[2.*scale]*48},'aFRR':{'up':[5.*scale]*48,'down':[4.*scale]*48},'mFRR':{'up':[8.*scale]*48,'down':[6.*scale]*48}},
        historical_rtbm_prices={str(t):[[100.]*6 for _ in range(3)] for t in range(48)},
        force_states=[dict(asset='ccgt_0',period=0,state='e0:0')] if name=='forced_long' else [])


def build_market(data,formulation='compact'):
    m=Market()
    for spec in data['plants']:
        spec=deepcopy(spec);m.add_participant(spec['participant']);m.add_configured_thermal(**spec,configuration_formulation=formulation)
    for spec in data['conventional']:
        spec=deepcopy(spec);m.add_participant(spec['participant']);spec['initial_state']=ThermalState(**spec['initial_state']);m.add_thermal(**spec)
    m.add_participant('res_owner');m.add_res('res','res_owner',generation_forecast=data['res_mw'],actual_generation=data['res_mw'])
    m.add_participant('supplier');m.add_supplier('load','supplier',demand_forecast=data['demand_mw'],actual_consumption=data['demand_mw'])
    return m


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--case',choices=('small','long','fleet','forced_long'),default='small');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(synthetic_case(args.case),indent=2));print(args.output)

if __name__=='__main__':main()

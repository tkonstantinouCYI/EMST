"""Configuration graph for one physical thermal plant (half-hour commitment).

Transition profiles are terminal MW at consecutive half-hours. Components listed
for a transition step are online for that entire step; clocks never reset merely
because the plant changes configuration.
"""
from copy import deepcopy
from dataclasses import dataclass
import math
import pulp as lp
from .model import PRODUCTS,UP,DOWN,RESPONSE_HOURS
from .physics import thermal_path
from .offers import asset_energy_prices

@dataclass(frozen=True)
class PlantState:
    configuration: str
    power_mw: float
    component_histories: dict
    transition: dict | None=None


def configured(asset):return 'plant_configurations' in asset.parameters


def number(value,name,minimum=0):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<minimum:
        raise ValueError('Invalid '+name)
    return float(value)


def validate(components,configurations,transitions,initial):
    if not components or not configurations or 'OFF' not in configurations:raise ValueError('Plant requires components and an OFF configuration')
    cs=deepcopy(components);cfg=deepcopy(configurations);edges=deepcopy(transitions)
    for name,c in cs.items():
        if not isinstance(name,str) or not name:raise ValueError('Component IDs must be nonempty strings')
        for key in ('minimum_up_minutes','minimum_down_minutes'):
            c[key]=number(c.get(key,0),key)
            if c[key]%30:raise ValueError('Component minimum times must be whole half-hours')
    for name,c in cfg.items():
        if not isinstance(name,str) or ':' in name or not name:raise ValueError('Invalid configuration identifier')
        c['components']=list(c.get('components',[]))
        if len(set(c['components']))!=len(c['components']) or set(c['components'])-cs.keys():raise ValueError('Unknown/duplicate configuration component')
        for key in ('minimum_mw','maximum_mw','marginal_cost','no_load_cost_per_hour','reserve_limit_mw'):
            c[key]=number(c.get(key,0),key)
        c['ramp_mw_hour']=number(c.get('ramp_mw_hour',1000),'configuration ramp',1e-12)
        if c['minimum_mw']>c['maximum_mw']:raise ValueError('Configuration minimum exceeds maximum')
        if name=='OFF' and (c['components'] or c['maximum_mw'] or c['minimum_mw'] or c['no_load_cost_per_hour'] or c['reserve_limit_mw']):raise ValueError('OFF must have no online components, output, reserves or no-load cost')
        if name!='OFF' and not c['components']:raise ValueError('Operating configuration requires components')
    ids=set()
    for e in edges:
        if not isinstance(e.get('id'),str) or not e['id'] or ':' in e['id'] or e['id'] in ids:raise ValueError('Transition IDs must be unique and contain no colon')
        ids.add(e['id'])
        if e.get('source') not in cfg or e.get('target') not in cfg or e['source']==e['target']:raise ValueError('Invalid transition edge')
        e['cost_eur']=number(e.get('cost_eur',0),'transition cost')
        operating=cfg[e['target'] if e['target']!='OFF' else e['source']]
        for field in ('marginal_cost','no_load_cost_per_hour'):
            e[field]=number(e.get(field,operating[field]),'transition '+field)
        profile=e.get('profile_mw');statuses=e.get('components_by_step')
        if not isinstance(profile,list) or not profile:raise ValueError('Transition requires a nonempty half-hour endpoint profile')
        e['profile_mw']=[number(v,'transition profile') for v in profile]
        if not isinstance(statuses,list) or len(statuses)!=len(profile):raise ValueError('Specify component status for every transition step')
        if any(set(v)-cs.keys() or len(set(v))!=len(v) for v in statuses):raise ValueError('Invalid transition components')
        if set(statuses[-1])!=set(cfg[e['target']]['components']):raise ValueError('Final transition component status must match target')
        limit=max(c['maximum_mw'] for c in cfg.values())
        if max(profile)>limit:raise ValueError('Transition exceeds plant maximum output')
        if not cfg[e['target']]['minimum_mw']<=profile[-1]<=cfg[e['target']]['maximum_mw']:raise ValueError('Transition does not reach target operating range')
        e['ramp_mw_hour']=number(e.get('ramp_mw_hour',max(cfg[e['source']]['ramp_mw_hour'],cfg[e['target']]['ramp_mw_hour'])),'transition ramp',1e-12)
        if any(abs(b-a)>.5*e['ramp_mw_hour']+1e-7 for a,b in zip(profile,profile[1:])):raise ValueError('Transition profile violates ramp limit')
    data=dict(components=cs,configurations=cfg,transitions=edges)
    state=initial_state(data,initial)
    return data,state


def states(data):
    result={name:dict(configuration=name,components=c['components'],minimum=c['minimum_mw'],maximum=c['maximum_mw'],ramp=c['ramp_mw_hour'],cost=c['marginal_cost'],no_load=c['no_load_cost_per_hour'],reserve=c['reserve_limit_mw']) for name,c in data['configurations'].items()}
    for e in data['transitions']:
        for step,endpoint in enumerate(e['profile_mw']):
            c=data['configurations'][e['target']]
            result[f"{e['id']}:{step}"]=dict(configuration=e['source'],components=e['components_by_step'][step],minimum=0.,maximum=max(v['maximum_mw'] for v in data['configurations'].values()),ramp=e['ramp_mw_hour'],cost=e.get('marginal_cost',c['marginal_cost']),no_load=e.get('no_load_cost_per_hour',c['no_load_cost_per_hour']),reserve=0.,edge=e,step=step,endpoint=endpoint)
    return result


def initial_state(data,value):
    value=PlantState(**value) if isinstance(value,dict) else value
    if not isinstance(value,PlantState) or value.configuration not in data['configurations']:raise ValueError('Expected valid PlantState')
    number(value.power_mw,'plant boundary power')
    if set(value.component_histories)!=set(data['components']):raise ValueError('Initial histories must cover every component')
    for c,h in value.component_histories.items():
        if set(h)!={'on','duration_minutes'} or type(h['on']) is not bool:raise ValueError('Component history requires boolean on and duration_minutes')
        number(h['duration_minutes'],'component duration')
    available=states(data)
    if value.transition:
        tr=value.transition
        if set(tr)!={'id','elapsed_minutes'}:raise ValueError('Carried transition requires id and elapsed_minutes')
        e=next((e for e in data['transitions'] if e['id']==tr['id']),None)
        if e is None or e['source']!=value.configuration:raise ValueError('Invalid carried transition')
        age=number(tr['elapsed_minutes'],'transition elapsed')
        if age>=30*len(e['profile_mw']) or age%5:raise ValueError('Invalid unfinished transition duration')
        key=f"{e['id']}:{int(age//30)}"
        # At a boundary, histories describe the previous completed interval.
        if age%30:expected=set(available[key]['components'])
        elif age:expected=set(e['components_by_step'][int(age//30)-1])
        else:expected=set(data['configurations'][value.configuration]['components'])
        if value.power_mw>available[key]['maximum']+1e-6:raise ValueError('Initial transition power exceeds capacity')
    else:
        c=data['configurations'][value.configuration]
        if not c['minimum_mw']-1e-6<=value.power_mw<=c['maximum_mw']+1e-6:raise ValueError('Boundary power outside initial configuration')
        expected=set(c['components'])
    if {c for c,h in value.component_histories.items() if h['on']}!=expected:raise ValueError('Component histories inconsistent with initial configuration/transition')
    return deepcopy(value)


def boundary(value):
    return dict(configuration=value.configuration,power_mw=value.power_mw,component_histories=deepcopy(value.component_histories),transition=deepcopy(value.transition),stage='dispatch' if value.configuration!='OFF' or value.transition else 'offline',duration_minutes=0,on=int(value.configuration!='OFF' or bool(value.transition)))


def key_at_boundary(state):
    tr=state.get('transition')
    return f"{tr['id']}:{int(tr['elapsed_minutes']//30)}" if tr else state['configuration']


def advance(asset,old,row,minutes):
    data=asset.parameters['plant_configurations'];key=row['configuration_state'];spec=states(data)[key]
    hist={c:dict(on=c in spec['components'],duration_minutes=h['duration_minutes']+minutes if h['on']==(c in spec['components']) else minutes) for c,h in old['component_histories'].items()}
    tr=None;configuration=spec['configuration']
    if 'edge' in spec:
        e=spec['edge'];prior=old.get('transition')
        elapsed=(prior['elapsed_minutes'] if prior and prior['id']==e['id'] else 0)+minutes
        if elapsed>=len(e['profile_mw'])*30:configuration=e['target']
        else:tr=dict(id=e['id'],elapsed_minutes=elapsed)
    return dict(configuration=configuration,power_mw=row['end_mw'],component_histories=hist,transition=tr,stage='dispatch' if configuration!='OFF' or tr else 'offline',duration_minutes=0,on=int(configuration!='OFF' or bool(tr)))


def build(model,asset,initial,intervals,p,end,paths,on,start,stop,reserve,terms,commercial,rules,*,rtbm,reference,immediate,mandatory):
    data=asset.parameters['plant_configurations'];specs=states(data);names=list(specs);n=len(intervals);cap=asset.parameters['capacity_mw'];prefix='plant_'+asset.id
    z={};flows={};components={};transition_cost={};operating_adjustment={};fixed={}
    initial_key=key_at_boundary(initial)
    # RTBM follows ISP's selected state. The remaining MTU is its only horizon.
    if rtbm:
        chosen=reference['configuration_state']
        if initial.get('transition') and chosen!=initial_key:raise ValueError('RTBM cannot interrupt a configuration transition')
        if not initial.get('transition') and chosen!=initial['configuration']:
            spec=specs[chosen]
            if 'edge' not in spec or spec['step']!=0 or spec['edge']['source']!=initial['configuration']:
                raise ValueError('RTBM configuration change requires the permitted transition')
        for j in range(n):
            for s in names:z[s,j]=int(s==chosen)
            fixed[j]=chosen
    else:
        if initial.get('transition') and initial['transition']['elapsed_minutes']%30:raise ValueError('ISP boundary must align unfinished configuration transitions to half-hours')
        edges=[]
        for e in data['transitions']:
            chain=[e['source']]+[f"{e['id']}:{i}" for i in range(len(e['profile_mw']))]+[e['target']]
            edges+=list(zip(chain,chain[1:]))
        # Completion establishes the target at the boundary; it need not occupy
        # an extra stable MTU before another permitted transition starts.
        for e in data['transitions']:
            for following in data['transitions']:
                if following['source']==e['target']:
                    edges.append((f"{e['id']}:{len(e['profile_mw'])-1}",f"{following['id']}:0"))
        for j in range(n):
            for s in names:z[s,j]=lp.LpVariable(f'{prefix}_state_{names.index(s)}_{j}',cat='Binary')
            model+=lp.lpSum(z[s,j] for s in names)==1
            # Inherited transition occupancy is fixed for the first MTU.
            if j==0 and initial.get('transition'):model+=z[initial_key,j]==1
            for k,(a,b) in enumerate(edges):
                flows[a,b,j]=lp.LpVariable(f'{prefix}_flow_{k}_{j}',0,1)
                previous=int(a==initial_key) if j==0 else z[a,j-1]
                # A carried transition is already executing its current step.
                if j==0 and initial.get('transition'):model+=flows[a,b,j]==0
                else:
                    model+=flows[a,b,j]<=previous;model+=flows[a,b,j]<=z[b,j]
            for s in names:
                previous=int(s==initial_key) if j==0 else z[s,j-1]
                model+=z[s,j]-previous==lp.lpSum(v for (a,b,t),v in flows.items() if t==j and b==s)-lp.lpSum(v for (a,b,t),v in flows.items() if t==j and a==s)
                if 'edge' in specs[s] and not (j==0 and initial.get('transition')):
                    model+=z[s,j]<=lp.lpSum(v for (a,b,t),v in flows.items() if t==j and b==s)
        # Component minimum on/off times apply across all virtual states.
        for ci,(component,limits) in enumerate(data['components'].items()):
            starts={};stops={}
            for j in range(n):
                status=lp.lpSum(z[s,j] for s in names if component in specs[s]['components']);components[component,j]=status
                previous=int(initial['component_histories'][component]['on']) if j==0 else components[component,j-1]
                starts[j]=lp.LpVariable(f'{prefix}_component_start_{ci}_{j}',cat='Binary');stops[j]=lp.LpVariable(f'{prefix}_component_stop_{ci}_{j}',cat='Binary')
                model+=status-previous==starts[j]-stops[j];model+=starts[j]+stops[j]<=1
                up=int(limits['minimum_up_minutes']/30);down=int(limits['minimum_down_minutes']/30)
                model+=lp.lpSum(starts[t] for t in range(max(0,j-up+1),j+1))<=status
                model+=lp.lpSum(stops[t] for t in range(max(0,j-down+1),j+1))<=1-status
                h=initial['component_histories'][component];remaining=limits['minimum_up_minutes' if h['on'] else 'minimum_down_minutes']-h['duration_minutes']
                if j*30<remaining:model+=status==int(h['on'])
    base=asset.parameters['marginal_cost']
    for j,t in enumerate(intervals):
        k=asset.id,j;dt=t.hours;previous=initial['power_mw'] if j==0 else end[asset.id,j-1]
        on[k]=1-z['OFF',j]
        entering=[]
        for e in data['transitions']:
            first=f"{e['id']}:0"
            if rtbm:
                entered=int(chosen==first and not initial.get('transition') and j==0)
            else:entered=lp.lpSum(v for (a,b,t),v in flows.items() if b==first and t==j)
            entering.append(entered*e['cost_eur'])
        transition_cost[j]=lp.lpSum(entering)
        start[k]=(int(initial['configuration']=='OFF' and chosen!='OFF' and not initial.get('transition') and j==0) if rtbm else lp.lpSum(v for e in data['transitions'] if e['source']=='OFF' for (a,b,t),v in flows.items() if b==f"{e['id']}:0" and t==j))
        stop[k]=0
        selected_power=[];adjust=[]
        end[k]=lp.LpVariable(f'{prefix}_end_{j}',0,cap)
        if rtbm:
            spec=specs[chosen]
            if 'edge' in spec:
                # Transition profiles remain binding in BOTH RTBM modes.
                remaining_minutes=30-(initial.get('transition') or {}).get('elapsed_minutes',0)%30
                node_count=t.minutes*2
                nodes=[previous]+[lp.LpVariable(f'{prefix}_path_{j}_{i}',0,cap) for i in range(1,node_count+1)]
                for i,node in enumerate(nodes[1:],1):
                    fraction=(j*t.minutes+i*.5)/remaining_minutes
                    model+=node==initial['power_mw']+(spec['endpoint']-initial['power_mw'])*fraction
                for a,b in zip(nodes,nodes[1:]):
                    model+=b-a<=spec['ramp']/120;model+=a-b<=spec['ramp']/120
                integral=(nodes[0]*.5+lp.lpSum(nodes[1:-1])+nodes[-1]*.5)/120
                paths[k]=nodes;model+=end[k]==nodes[-1];model+=p[k]*dt==integral
            elif immediate:
                model+=end[k]==p[k];paths[k]=[end[k],end[k]]
                model+=p[k]>=spec['minimum'];model+=p[k]<=spec['maximum']
            else:
                paths[k],integral=thermal_path(model,f'{prefix}_path_{j}',previous,dt,t.minutes*2,spec['maximum'],int(chosen!='OFF'),spec['minimum'],spec['ramp'])
                model+=end[k]==paths[k][-1];model+=p[k]*dt==integral
            adjust.append((spec['cost']-base)*p[k]*dt+spec['no_load']*dt)
        else:
            for si,s in enumerate(names):
                spec=specs[s];power=lp.LpVariable(f'{prefix}_power_{si}_{j}',0,cap)
                model+=power<=cap*z[s,j];selected_power.append(power)
                adjust.append((spec['cost']-base)*power*dt+spec['no_load']*dt*z[s,j])
                if 'edge' in spec:
                    target=spec['endpoint'];model+=end[k]-target<=cap*(1-z[s,j]);model+=target-end[k]<=cap*(1-z[s,j])
                    model+=power-(previous+target)/2<=cap*(1-z[s,j]);model+=(previous+target)/2-power<=cap*(1-z[s,j])
                else:
                    model+=power>=spec['minimum']*z[s,j];model+=power<=spec['maximum']*z[s,j]
                    model+=end[k]-p[k]<=cap*(1-z[s,j]);model+=p[k]-end[k]<=cap*(1-z[s,j])
                model+=end[k]-previous<=spec['ramp']*dt+2*cap*(1-z[s,j]);model+=previous-end[k]<=spec['ramp']*dt+2*cap*(1-z[s,j])
            model+=p[k]==lp.lpSum(selected_power)
        if mandatory[j]:model+=z['OFF',j]==0
        if not rtbm:
            rup=[];rdn=[]
            for product in PRODUCTS:
                reserve[asset.id,j,product]=lp.LpVariable(f'{prefix}_reserve_{product}_{j}',0)
                model+=reserve[asset.id,j,product]<=lp.lpSum(min(spec['reserve'],spec['ramp']*RESPONSE_HOURS[product])*z[s,j] for s,spec in specs.items())
                terms.append(reserve[asset.id,j,product]*asset.parameters['reserve_prices'][product])
            rup=lp.lpSum(reserve[asset.id,j,x] for x in UP);rdn=lp.lpSum(reserve[asset.id,j,x] for x in DOWN)
            model+=p[k]+rup<=lp.lpSum(spec['maximum']*z[s,j] for s,spec in specs.items())
            model+=p[k]-rdn>=lp.lpSum(spec['minimum']*z[s,j] for s,spec in specs.items())
        operating_adjustment[j]=lp.lpSum(adjust)
        terms.extend([transition_cost[j],operating_adjustment[j]])
    return dict(specs=specs,z=z,transition_cost=transition_cost,operating_adjustment=operating_adjustment,initial=deepcopy(initial))


def row(data,j,asset,interval,rtbm):
    chosen=next(s for s in data['specs'] if lp.value(data['z'][s,j])>.5);spec=data['specs'][chosen]
    transition=None
    if 'edge' in spec:transition=dict(id=spec['edge']['id'],source=spec['edge']['source'],target=spec['edge']['target'],step=spec['step'],duration_minutes=30*len(spec['edge']['profile_mw']))
    return dict(configuration=spec['configuration'],configuration_state=chosen,configuration_binaries={s:round(lp.value(v)) for (s,t),v in data['z'].items() if t==j},
        configuration_transition=transition,online_components=list(spec['components']),transition_cost_eur=float(lp.value(data['transition_cost'][j]) or 0),
        configuration_operating_adjustment_eur=float(lp.value(data['operating_adjustment'][j]) or 0),
        effective_marginal_cost=spec['cost'],effective_no_load_cost_per_hour=spec['no_load'],effective_ramp_mw_hour=spec['ramp'],
        phase='dispatch' if chosen!='OFF' else 'offline',phase_binaries={q:int(q=='dispatch' and chosen!='OFF') for q in ('sync','soak','dispatch','desync')},phase_elapsed_before_minutes=0,startup_cost_eur=0.)

def check_rows(asset,initial,rows):
    """Replay legal paths, component clocks, profile energy and single occupancy."""
    data=asset.parameters['plant_configurations'];specs=states(data);old=deepcopy(initial)
    for r in rows:
        key=r['configuration_state'];spec=specs[key];minutes=r['hours']*60
        if sum(r['configuration_binaries'].values())!=1 or r['configuration_binaries'].get(key)!=1:return False
        expected=key_at_boundary(old)
        if old.get('transition'):
            if key!=expected:return False
        elif key!=old['configuration']:
            if 'edge' not in spec or spec['step']!=0 or spec['edge']['source']!=old['configuration']:return False
        for c,h in old['component_histories'].items():
            status=c in spec['components']
            if status!=h['on'] and h['duration_minutes']+1e-6<data['components'][c]['minimum_up_minutes' if h['on'] else 'minimum_down_minutes']:return False
        endpoint=r.get('end_mw',r['configuration_endpoint_mw'])
        if 'edge' in spec:
            elapsed=(old.get('transition') or {}).get('elapsed_minutes',0)%30
            expected_end=old['power_mw']+(spec['endpoint']-old['power_mw'])*minutes/(30-elapsed)
            if abs(endpoint-expected_end)>1e-5 or abs(r['energy_mwh']-(old['power_mw']+endpoint)*.5*r['hours'])>1e-5:return False
            if abs(endpoint-old['power_mw'])>spec['ramp']*r['hours']+1e-5:return False
        elif not spec['minimum']-1e-5<=r['power_mw']<=spec['maximum']+1e-5:return False
        copy=dict(r,end_mw=endpoint);old=advance(asset,old,copy,minutes)
    return True

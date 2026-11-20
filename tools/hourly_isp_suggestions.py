"""Experimental 24-step hourly model for the synthetic ISP handoff.

This is NOT a public hourly Market.run API. One existing 30-minute kernel step
represents one real hour by an explicit change of time/energy units. Rates,
durations, energy prices and reserve response constants are transformed together.
The hourly copy is rounded. It can be solved directly without half-hour repair.
The optional legacy expansion helper below is not used by the hourly benchmark.
"""
from copy import deepcopy
import math,time
from unittest.mock import patch
import pulp as lp
from emst import configurations as cfg
from emst import model as constants
import emst.dispatch as dispatch_module
from emst.offers import asset_energy_prices


def nearest_hours(minutes):
    if not math.isfinite(minutes) or minutes<0:raise ValueError('Invalid duration')
    return int(math.floor(minutes/60+.5))


def resample_endpoints(values,start):
    if not values:return []
    count=max(1,nearest_hours(len(values)*30));old=[float(start)]+list(values)
    result=[]
    for i in range(1,count+1):
        x=i*len(values)/count;k=min(int(x),len(values)-1);f=x-k
        result.append(old[k]*(1-f)+old[k+1]*f)
    return result


def hourly_copy(original, configuration_formulation='compact'):
    """Return encoded hourly copy + real-unit audit snapshot + response scaling."""
    if len(original['intervals'])!=48:raise ValueError('This prototype needs a 48-MTU non-DST day')
    if any(a.technology=='storage' for a in original['assets']):raise NotImplementedError('Prototype covers the handoff thermal/RES fleet only')
    k=deepcopy(original);rounding=[]
    for a in k['assets']:
        if a.technology!='thermal':continue
        p=a.parameters;s=k['states'][a.id]
        if s.get('transition') or s['stage'] not in ('dispatch','offline'):
            raise NotImplementedError('Hourly prototype requires stable initial thermal states, as in the handoff')
        up,dn=asset_energy_prices(a,k['rules']);p['energy_up_price']=up;p['energy_down_price']=dn
        for key in ('minimum_up_minutes','minimum_down_minutes','startup_minutes','shutdown_minutes'):
            old=p[key];p[key]=60*nearest_hours(old)
            if p[key]!=old:rounding.append(dict(asset=a.id,parameter=key,old_minutes=old,rounded_minutes=p[key]))
        s['duration_minutes']=60*nearest_hours(s['duration_minutes'])
        if cfg.configured(a):
            data=p['plant_configurations']
            for c,v in data['components'].items():
                for key in ('minimum_up_minutes','minimum_down_minutes'):v[key]=60*nearest_hours(v[key])
                s['component_histories'][c]['duration_minutes']=60*nearest_hours(s['component_histories'][c]['duration_minutes'])
            for e in data['transitions']:
                old=len(e['profile_mw'])*30;new=resample_endpoints(e['profile_mw'],data['configurations'][e['source']]['minimum_mw'])
                for field,reducer in (('reserve_minimum_mw',max),('reserve_maximum_mw',min)):
                    if field in e:
                        previous=e[field];size=len(previous)
                        e[field]=[reducer(previous[int(math.floor(h*size/len(new))):int(math.ceil((h+1)*size/len(new)))]) for h in range(len(new))]
                membership=e['components_by_step']
                # Handoff declares target component set throughout each transition.
                if any(set(x)!=set(membership[0]) for x in membership):raise NotImplementedError('Nonconstant transition component sets need an explicit hourly convention')
                e['profile_mw']=new;e['components_by_step']=[list(membership[0]) for _ in new]
                rounding.append(dict(asset=a.id,parameter='transition:'+e['id'],old_minutes=old,rounded_minutes=60*len(new)))
            p['configuration_formulation']=configuration_formulation
        else:
            profile=p['phase_profiles_mw'];start={'sync':0.,'soak':profile['sync'][-1] if profile['sync'] else 0.,'desync':p['minimum_mw']}
            if profile['sync']:raise NotImplementedError('Handoff has no separate sync profile')
            p['phase_profiles_mw']={q:resample_endpoints(v,start[q]) for q,v in profile.items()}
    # Real hourly data before changing kernel units. MWh add; MW average;
    # reserve/mandatory requirements take the maximum of each half-hour pair.
    average=lambda x:[(x[2*h]+x[2*h+1])/2 for h in range(24)]
    peak=lambda x:[max(x[2*h],x[2*h+1]) for h in range(24)]
    real_commercial={a:[x[2*h]+x[2*h+1] for h in range(24)] for a,x in k['commercial'].items()}
    k['demand']=average(k['demand']);k['res']=average(k['res'])
    k['requirements']={p:peak(x) for p,x in k['requirements'].items()}
    k['mandatory_on']={a:peak(x) for a,x in k['mandatory_on'].items()}
    k['res_forecasts']={a:average(x) for a,x in k['res_forecasts'].items()}
    audit=dict(real_interval_minutes=60,real_horizon_hours=24,rounding_policy='nearest hour, half up; positive profiles at least one hour',
        interval_starts=[original['intervals'][2*h].start.isoformat() for h in range(24)],
        demand_mw=k['demand'],res_mw=k['res'],commercial_mwh=real_commercial,requirements_mw=k['requirements'],
        rounding=rounding,assets=[dict(id=a.id,parameters=deepcopy(a.parameters)) for a in k['assets']],states=deepcopy(k['states']))
    # Kernel .5h is redefined as one real hour. Native energy is half real MWh.
    k['intervals']=k['intervals'][:24];k['commercial']={a:[v/2 for v in x] for a,x in real_commercial.items()}
    for a in k['assets']:
        p=a.parameters
        if a.technology=='res':p['energy_down_price']*=2;continue
        if a.technology!='thermal':continue
        for key in ('minimum_up_minutes','minimum_down_minutes','startup_minutes','shutdown_minutes'):p[key]/=2
        for key in ('ramp_mw_hour','marginal_cost','no_load_cost_per_hour','reserve_cost','energy_up_price','energy_down_price'):p[key]*=2
        p['reserve_prices']={product:v*2 for product,v in p['reserve_prices'].items()}
        s=k['states'][a.id];s['duration_minutes']/=2
        if cfg.configured(a):
            data=p['plant_configurations']
            for v in data['components'].values():
                v['minimum_up_minutes']/=2;v['minimum_down_minutes']/=2
            for h in s['component_histories'].values():h['duration_minutes']/=2
            for v in data['configurations'].values():
                v['ramp_mw_hour']*=2;v['marginal_cost']*=2;v['no_load_cost_per_hour']*=2
            for e in data['transitions']:e['ramp_mw_hour']*=2
    slack=k['rules']['slack'];slack['energy_shortage_eur_mwh']*=2;slack['energy_surplus_eur_mwh']*=2
    slack['reserve_shortage_eur_mw_mtu']={p:v*2 for p,v in slack['reserve_shortage_eur_mw_mtu'].items()}
    responses={p:v/2 for p,v in constants.RESPONSE_HOURS.items()}
    audit['kernel_encoding']=dict(step_minutes=30,real_minutes_per_kernel_minute=2,energy_MWh_per_real_MWh=.5,
        ramp_rate_multiplier=2,energy_price_multiplier=2,reserve_response_time_multiplier=.5)
    return k,audit,responses


def capture(kwargs,responses=None):
    result={};started=time.perf_counter()
    class Captured(Exception):pass
    def intercept(problem,mip=True):
        assert mip;result.update(model=problem,construction_seconds=time.perf_counter()-started);raise Captured()
    with patch.object(dispatch_module,'solve',side_effect=intercept),patch.dict(constants.RESPONSE_HOURS,responses or constants.RESPONSE_HOURS):
        try:dispatch_module.dispatch(**kwargs)
        except Captured:pass
    return result


def decisions(model,assets):
    rows=[];thermal=[a for a in assets if a.technology=='thermal']
    variables={v.name:v for v in model.variables()}
    for h in range(24):
        group={}
        for i,a in enumerate(thermal):
            if cfg.configured(a):
                z=model._emst_configuration_states[a.id]
                active=[s for (s,t),expr in z.items() if t==h and lp.value(expr)>.5];assert len(active)==1
                group[a.id]=dict(configuration_state=active[0])
            else:
                phase=next((q for q in ('sync','soak','dispatch','desync') if variables[f'thermal_{i}_{q}_{h}'].varValue>.5),'offline')
                group[a.id]=dict(phase=phase)
        rows.append(dict(hour=h,assets=group))
    return rows


def expand(hour_rows,original_assets,hour_assets):
    """Preserve start instants; use ORIGINAL half-hour steps, then stable padding."""
    by_hour={r['hour']:r['assets'] for r in hour_rows};hour_assets={a.id:a for a in hour_assets}
    paths={}
    for a in original_assets:
        if a.technology!='thermal':continue
        path=[None]*48;coarse=hour_assets[a.id]
        for h in range(24):
            if cfg.configured(a):
                key=by_hour[h][a.id]['configuration_state']
                if ':' in key:
                    edge_id,step=key.rsplit(':',1)
                    if int(step):continue
                    e=next(e for e in a.parameters['plant_configurations']['transitions'] if e['id']==edge_id)
                    ec=next(e for e in coarse.parameters['plant_configurations']['transitions'] if e['id']==edge_id)
                    block=[f'{edge_id}:{i}' for i in range(len(e['profile_mw']))]
                    block += [e['target']]*(2*len(ec['profile_mw'])-len(block))
                else:block=[key,key]
            else:
                key=by_hour[h][a.id]['phase']
                if key in ('sync','soak','desync'):
                    if h and by_hour[h-1][a.id]['phase']==key:continue
                    size=len(a.parameters['phase_profiles_mw'][key]);coarse_size=len(coarse.parameters['phase_profiles_mw'][key])
                    target='offline' if key=='desync' else 'dispatch'
                    block=[key]*size+[target]*(2*coarse_size-size)
                else:block=[key,key]
            for k,key in enumerate(block):
                t=2*h+k
                if t<48:path[t]=key
        assert all(s is not None for s in path),(a.id,path)
        paths[a.id]=path
    return dict(rows=[dict(period=t,assets={a.id:({ 'configuration_state':paths[a.id][t]} if cfg.configured(a) else {'phase':paths[a.id][t]}) for a in original_assets if a.id in paths}) for t in range(48)])

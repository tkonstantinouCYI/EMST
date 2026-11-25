"""Optional rectangular ON/OFF thermal approximation; no staged startup energy."""
import math
import pulp as lp
from .phases import PHASES,canonical


def enabled(asset):return asset.parameters.get('commitment_model','staged')=='on_off'


def build(model,name,intervals,initial,parameters,delivery_minutes=None):
    minutes=intervals[0].minutes;n=len(intervals)
    if minutes not in (30,60) or any(t.minutes!=minutes for t in intervals):raise ValueError('ON/OFF ISP requires uniform 30- or 60-minute intervals')
    stage=canonical(initial['stage'])
    if stage not in ('offline','dispatch'):raise ValueError('ON/OFF initial state must be dispatch or offline')
    u={j:lp.LpVariable(f'{name}_dispatch_{j}',cat='Binary') for j in range(n)}
    start={j:lp.LpVariable(f'{name}_start_{j}',0,1) for j in range(n)}
    stop={j:lp.LpVariable(f'{name}_stop_{j}',0,1) for j in range(n)}
    for j in range(n):
        previous=int(stage=='dispatch') if j==0 else u[j-1]
        model+=u[j]-previous==start[j]-stop[j]
        model+=start[j]<=u[j];model+=start[j]<=1-previous
        model+=stop[j]<=previous;model+=stop[j]<=1-u[j]
        up=max(1,math.ceil(parameters['minimum_up_minutes']/minutes))
        down=max(1,math.ceil(parameters['minimum_down_minutes']/minutes))
        model+=lp.lpSum(start[k] for k in range(max(0,j-up+1),j+1))<=u[j]
        model+=lp.lpSum(stop[k] for k in range(max(0,j-down+1),j+1))<=1-u[j]
        remaining=parameters['minimum_up_minutes' if stage=='dispatch' else 'minimum_down_minutes']-initial['duration_minutes']
        if j*minutes<remaining:model+=u[j]==int(stage=='dispatch')
        if stage=='dispatch' and j>0:
            available=((j-1)*minutes+(delivery_minutes or minutes))/60
            if initial['power_mw']-parameters['minimum_mw']>parameters['ramp_down_mw_hour']*available+1e-7:
                model+=stop[j]==0
    return dict(on=u,start=start,stop=stop,flags={(q,j):u[j] if q=='dispatch' else 0 for q in PHASES for j in range(n)},
        minutes=minutes,initial_stage=stage,initial_age=initial['duration_minutes'],binary_count=n)


def bind(model,data,j,power,previous,initial,parameters,reserves):
    u=data['on'][j];v=data['start'][j];w=data['stop'][j]
    prior=int(data['initial_stage']=='dispatch') if j==0 else data['on'][j-1]
    cap=parameters['capacity_mw'];minimum=parameters['minimum_mw'];dt=data['minutes']/60
    model+=power>=minimum*u
    model+=power<=minimum+(cap-minimum)*(1-v)
    model+=previous<=minimum+(cap-minimum)*(1-w)
    model+=power-previous<=parameters['ramp_up_mw_hour']*dt+cap*(2-u-prior)
    model+=previous-power<=parameters['ramp_down_mw_hour']*dt+cap*(2-u-prior)
    last=data['stop'].get(j+1,0)
    for award in reserves:
        model+=award<=parameters['reserve_limit_mw']*(1-v)
        model+=award<=parameters['reserve_limit_mw']*(1-last)


def row(data,j):
    on=int(round(lp.value(data['on'][j])));phase='dispatch' if on else 'offline'
    age=data['initial_age'];prior=data['initial_stage']
    for k in range(j):
        current='dispatch' if lp.value(data['on'][k])>.5 else 'offline'
        age=age+data['minutes'] if current==prior else data['minutes'];prior=current
    if phase!=prior:age=0
    return dict(phase=phase,phase_binaries={q:int(q=='dispatch' and on) for q in PHASES},phase_elapsed_before_minutes=age,
        commitment_model='on_off',boundary_minimum=bool(lp.value(data['start'][j])>.5 or lp.value(data['stop'].get(j+1,0))>.5),
        first_on_interval=bool(lp.value(data['start'][j])>.5),last_on_interval=bool(lp.value(data['stop'].get(j+1,0))>.5),
        boundary_power_policy='technical_minimum',boundary_reserve_policy='none',power_convention='constant_interval_MW')


def audit(rows,initial,parameters):
    """Replay discrete events, actual durations and boundary powers independently."""
    checks=dict(bounds=True,events=True,dwell=True,boundary_power=True,boundary_reserves=True,ramps=True,history=True)
    prior=int(canonical(initial['stage'])=='dispatch');age=initial['duration_minutes'];power=initial['power_mw']
    for j,r in enumerate(rows):
        on=r['on'];p=r['power_mw'];minutes=r['hours']*60
        checks['bounds'] &= (parameters['minimum_mw']-1e-5<=p<=parameters['capacity_mw']+1e-5) if on else abs(p)<1e-5
        checks['events'] &= r['start']==int(on and not prior) and r['stop']==int(prior and not on)
        if on!=prior:
            checks['dwell'] &= age+1e-5>=parameters['minimum_up_minutes' if prior else 'minimum_down_minutes']
            checks['boundary_power'] &= abs((power if prior else p)-parameters['minimum_mw'])<1e-5
        if on and prior:
            checks['ramps'] &= p-power<=parameters['ramp_up_mw_hour']*r['hours']+1e-5 and power-p<=parameters['ramp_down_mw_hour']*r['hours']+1e-5
        checks['history'] &= abs(r['phase_elapsed_before_minutes']-(age if on==prior else 0))<1e-5
        if r['boundary_minimum']:
            checks['boundary_power'] &= abs(p-parameters['minimum_mw'])<1e-5
            checks['boundary_reserves'] &= all(abs(v)<1e-5 for v in r['reserve_mw'].values())
        age=age+minutes if on==prior else minutes;prior=on;power=p
    return checks

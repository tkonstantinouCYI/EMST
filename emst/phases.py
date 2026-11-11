"""Four thermal phase binaries; offline is the all-zero state.

Phase profiles contain terminal MW for consecutive half-hours. Transition flows
are continuous auxiliaries: one-hot source/target states make them integral.
"""
import math
import pulp as lp

PHASES=('sync','soak','dispatch','desync')
ALIASES={'online':'dispatch','startup':'soak','shutdown':'desync'}


def canonical(stage):
    stage=ALIASES.get(stage,stage)
    if stage not in ('offline',)+PHASES:raise ValueError('Unknown thermal phase')
    return stage


def profiles(values,capacity,minimum,*,startup_minutes=0,shutdown_minutes=0):
    if values is None:
        for minutes in (startup_minutes,shutdown_minutes):
            if not math.isfinite(minutes) or minutes<0 or minutes%30:
                raise ValueError('Thermal phase durations must be whole half-hours')
        n=int(startup_minutes/30);d=int(shutdown_minutes/30)
        values={'sync':[], 'soak':[minimum*(i+1)/n for i in range(n)],
                'desync':[minimum*(1-(i+1)/d) for i in range(d)] if d else [0.]}
    if not isinstance(values,dict) or set(values)-{'sync','soak','desync'}:
        raise ValueError('Phase profiles accept sync, soak and desync only')
    result={q:tuple(float(x) for x in values.get(q,())) for q in ('sync','soak','desync')}
    if any(not math.isfinite(x) or not 0<=x<=capacity for v in result.values() for x in v):
        raise ValueError('Invalid thermal phase endpoint MW')
    if result['desync'] and result['desync'][-1]!=0:
        raise ValueError('Desync profile must finish at zero MW')
    startup=result['soak'] or result['sync']
    if startup and startup[-1]<minimum:
        raise ValueError('Startup phase profiles must reach minimum dispatch output')
    return result


def unit_profiles(unit):
    if unit.phase_profiles_mw is not None:
        return profiles(unit.phase_profiles_mw,unit.maximum_mw,unit.minimum_mw)
    return profiles({'sync':[], 'soak':unit.startup_trajectory_mw,
                     'desync':unit.shutdown_trajectory_mw or (0.,)},unit.maximum_mw,unit.minimum_mw)


def boundary_state(stage,elapsed,profile):
    """Return the state at an interval END, advancing completed fixed phases."""
    stage=canonical(stage)
    while stage in profile and elapsed>=30*len(profile[stage]):
        duration=30*len(profile[stage]);elapsed-=duration
        stage={'sync':('soak' if profile['soak'] else 'dispatch'),
               'soak':'dispatch','desync':'offline'}[stage]
    return stage,elapsed


def build(model,name,count,initial_stage,age_minutes,profile,minimum_up_minutes,minimum_down_minutes):
    """Build half-hour phase occupancy, legal transitions and exact dwell times."""
    initial_stage=canonical(initial_stage)
    if initial_stage in profile:
        if not profile[initial_stage] or age_minutes%30 or not 0<=age_minutes<30*len(profile[initial_stage]):
            raise ValueError('Initial phase needs an unfinished profile and half-hour-aligned elapsed time')
    chain=['offline']+[q for q in ('sync','soak') if profile[q]]+['dispatch']+(['desync'] if profile['desync'] else [])
    edges=list(zip(chain,chain[1:]+chain[:1]))
    flags={(q,t):lp.LpVariable(f'{name}_{q}_{t}',cat='Binary') for q in PHASES for t in range(count)}
    off={t:1-lp.lpSum(flags[q,t] for q in PHASES) for t in range(count)}
    flows={(a,b,t):lp.LpVariable(f'{name}_enter_{b}_{t}',0,1) for a,b in edges for t in range(count)}
    def state(q,t):return off[t] if q=='offline' else flags[q,t]
    def previous(q,t):return int(initial_stage==q) if t==0 else state(q,t-1)
    incoming={(q,t):lp.lpSum(flows[a,b,t] for a,b in edges if b==q) for q in chain for t in range(count)}
    for t in range(count):
        model+=off[t]>=0
        for q in PHASES:
            if q not in chain:model+=flags[q,t]==0
        for a,b in edges:
            model+=flows[a,b,t]<=previous(a,t)
            model+=flows[a,b,t]<=state(b,t)
        for q in chain:
            model+=state(q,t)-previous(q,t)==incoming[q,t]-lp.lpSum(flows[a,b,t] for a,b in edges if a==q)
        for q in ('sync','soak','desync'):
            if not profile[q]:continue
            length=len(profile[q]);inherited=int(initial_stage==q and t<length-age_minutes//30)
            model+=flags[q,t]==lp.lpSum(incoming[q,k] for k in range(max(0,t-length+1),t+1))+inherited
    first=chain[1]
    start={t:flows['offline',first,t] for t in range(count)}
    stop={t:incoming['offline',t] for t in range(count)}
    on={t:1-off[t] for t in range(count)}
    up=max(1,math.ceil(minimum_up_minutes/30));down=max(1,math.ceil(minimum_down_minutes/30))
    for t in range(count):
        model+=lp.lpSum(start[k] for k in range(max(0,t-up+1),t+1))<=on[t]
        model+=lp.lpSum(stop[k] for k in range(max(0,t-down+1),t+1))<=off[t]
    # Elapsed dispatch time is a conservative lower bound on online history.
    online_age=age_minutes
    if initial_stage=='soak':online_age+=len(profile['sync'])*30
    if initial_stage=='dispatch':online_age+=(len(profile['sync'])+len(profile['soak']))*30
    if initial_stage=='desync':online_age=minimum_up_minutes
    # A finite desync cannot finish too early just because its following offline
    # interval falls beyond this optimisation horizon.
    shutdown_target='desync' if profile['desync'] else 'offline'
    shutdown_length=len(profile['desync'])
    for t in range(count):
        shutdown=flows['dispatch',shutdown_target,t]
        for k in range(t+1):
            if (t+shutdown_length-k)*30<minimum_up_minutes:model+=shutdown+start[k]<=1
        if initial_stage not in ('offline','desync') and online_age+(t+shutdown_length)*30<minimum_up_minutes:
            model+=shutdown==0
    remaining=(minimum_down_minutes-age_minutes) if initial_stage=='offline' else (minimum_up_minutes-online_age)
    for t in range(count):
        if t*30<remaining:model+=on[t]==int(initial_stage!='offline')
    return dict(flags=flags,off=off,on=on,start=start,stop=stop,incoming=incoming,
                initial_stage=initial_stage,age_minutes=age_minutes,profiles=profile,count=count)


def bind_endpoint(model,data,t,endpoint,capacity):
    for q,values in data['profiles'].items():
        if not values:continue
        target=lp.lpSum(values[t-k]*data['incoming'][q,k] for k in range(max(0,t-len(values)+1),t+1))
        if data['initial_stage']==q:
            index=int(data['age_minutes']//30)+t
            if index<len(values):target+=values[index]
        flag=data['flags'][q,t]
        model+=endpoint-target<=capacity*(1-flag)
        model+=target-endpoint<=capacity*(1-flag)



def average_profiles(profile, minimum_mw):
    starts={'sync':0., 'soak':profile['sync'][-1] if profile['sync'] else 0., 'desync':minimum_mw}
    return {phase:tuple(((values[i-1] if i else starts[phase])+value)/2
                        for i,value in enumerate(values))
            for phase,values in profile.items()}


def bind_average_power(model,data,t,power,capacity,minimum_mw,initial_power):
    averages=average_profiles(data['profiles'],minimum_mw)
    if t==0 and data['initial_stage'] in averages:
        phase=data['initial_stage'];index=int(data['age_minutes']//30)
        values=list(averages[phase])
        values[index]=(initial_power+data['profiles'][phase][index])/2
        averages[phase]=tuple(values)
    bind_endpoint(model,{**data,'profiles':averages},t,power,capacity)


def row_phase(data,t):
    flags={q:int(round(lp.value(data['flags'][q,t]))) for q in PHASES}
    phase=next((q for q,v in flags.items() if v),'offline')
    age=data['age_minutes'] if data['initial_stage']==phase else 0
    for k in range(t):
        prior=next((q for q in PHASES if lp.value(data['flags'][q,k])>.5),'offline')
        if prior==phase:age+=30
        else:age=0
    return phase,flags,age


def check_rows(rows,initial_stage,initial_age,profile,minimum_mw=None,initial_power_mw=None):
    """Independent phase/dwell/profile audit of solved half-hour rows."""
    chain=['offline']+[q for q in ('sync','soak') if profile[q]]+['dispatch']+(['desync'] if profile['desync'] else [])
    successor=dict(zip(chain,chain[1:]+chain[:1]))
    previous=canonical(initial_stage);elapsed=initial_age
    checks=dict(one_hot=True,transitions_and_durations=True,phase_profiles=True,reserve_eligibility=True,startup_once=True)
    minimum_mw=minimum_mw if minimum_mw is not None else max((v[-1] for k,v in profile.items() if k!='desync' and v),default=0.)
    averages=average_profiles(profile,minimum_mw)
    for position,row in enumerate(rows):
        phase=row['phase'];flags=row['phase_binaries']
        checks['one_hot'] &= set(flags)==set(PHASES) and all(x in (0,1) for x in flags.values()) and sum(flags.values())==int(phase!='offline') and (phase=='offline' or flags.get(phase)==1)
        if previous in profile:
            allowed=[previous] if elapsed<30*len(profile[previous]) else [successor[previous]]
        else:allowed=[previous,successor[previous]]
        checks['transitions_and_durations'] &= phase in allowed
        before=elapsed if phase==previous else 0
        checks['transitions_and_durations'] &= row['phase_elapsed_before_minutes']==before
        if phase in profile:
            index=int(before//30)
            if 'nodes_mw' in row:
                checks['phase_profiles'] &= index<len(profile[phase]) and abs(row['end_mw']-profile[phase][index])<1e-5
                nodes=row['nodes_mw']
                checks['phase_profiles'] &= all(abs(v-(nodes[0]+(nodes[-1]-nodes[0])*k/(len(nodes)-1)))<1e-5 for k,v in enumerate(nodes))
            else:
                expected=averages[phase][index]
                if position==0 and initial_power_mw is not None and canonical(initial_stage)==phase:
                    expected=(initial_power_mw+profile[phase][index])/2
                checks['phase_profiles'] &= abs(row['power_mw']-expected)<1e-5
        if phase!='dispatch':checks['reserve_eligibility'] &= all(abs(x)<1e-6 for x in row['reserve_mw'].values())
        checks['startup_once'] &= row['start']==int(previous=='offline' and phase!='offline')
        elapsed=before+30;previous=phase
    return checks

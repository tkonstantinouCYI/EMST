"""Exact transition-start convolution formulation; see docs/compact-configurations.md."""
import pulp as lp


def occupancy(model,data,specs,names,n,prefix,initial,initial_key):
    stable=list(data['configurations']);edges=data['transitions']
    q={(c,t):lp.LpVariable(f'{prefix}_state_{names.index(c)}_{t}',cat='Binary') for c in stable for t in range(n)}
    x={(e['id'],t):lp.LpVariable(f'{prefix}_transition_start_{i}_{t}',cat='Binary') for i,e in enumerate(edges) for t in range(n)}
    inherited=initial.get('transition');old_edge=None;offset=remaining=0
    if inherited:
        old_edge=next(e for e in edges if e['id']==inherited['id'])
        offset=int(inherited['elapsed_minutes']//30);remaining=len(old_edge['profile_mw'])-offset
    z=dict(q)
    for e in edges:
        for k in range(len(e['profile_mw'])):
            for t in range(n):
                carried=int(old_edge is not None and e['id']==old_edge['id'] and t<remaining and k==offset+t)
                z[f"{e['id']}:{k}",t]=lp.LpAffineExpression(x[e['id'],t-k] if t>=k else 0)+carried
    for t in range(n):
        for c in stable:
            if t==0:available=int(not inherited and initial['configuration']==c)
            else:available=q[c,t-1]
            completed=lp.lpSum(x[e['id'],t-len(e['profile_mw'])] for e in edges if e['target']==c and t>=len(e['profile_mw']))
            carried_completion=int(old_edge is not None and t==remaining and old_edge['target']==c)
            model+=q[c,t]+lp.lpSum(x[e['id'],t] for e in edges if e['source']==c)==available+completed+carried_completion,f'{prefix}_conservation_{stable.index(c)}_{t}'
        model+=lp.lpSum(z[s,t] for s in names)==1,f'{prefix}_exclusive_{t}'
    # Exact event hull: for integer on/off status these continuous auxiliaries
    # have unique integer values. The two-equation reference relaxation alone
    # would allow spurious 0.5/0.5 starts/stops and is NOT sufficient.
    for i,(component,limits) in enumerate(data['components'].items()):
        status={t:lp.lpSum(z[s,t] for s in names if component in specs[s]['components']) for t in range(n)}
        starts={t:lp.LpVariable(f'{prefix}_component_start_{i}_{t}',0,1) for t in range(n)}
        stops={t:lp.LpVariable(f'{prefix}_component_stop_{i}_{t}',0,1) for t in range(n)}
        for t in range(n):
            previous=int(initial['component_histories'][component]['on']) if t==0 else status[t-1]
            model+=status[t]-previous==starts[t]-stops[t]
            model+=starts[t]<=status[t];model+=starts[t]<=1-previous
            model+=stops[t]<=previous;model+=stops[t]<=1-status[t]
            up=int(limits['minimum_up_minutes']/30);down=int(limits['minimum_down_minutes']/30)
            model+=lp.lpSum(starts[k] for k in range(max(0,t-up+1),t+1))<=status[t]
            model+=lp.lpSum(stops[k] for k in range(max(0,t-down+1),t+1))<=1-status[t]
            h=initial['component_histories'][component]
            left=limits['minimum_up_minutes' if h['on'] else 'minimum_down_minutes']-h['duration_minutes']
            if t*30<left:model+=status[t]==int(h['on'])
    return z,x,dict(occupancy_binary_count=len(q),transition_start_binary_count=len(x),
        component_event_binary_count=0,commitment_binary_count=len(q)+len(x))

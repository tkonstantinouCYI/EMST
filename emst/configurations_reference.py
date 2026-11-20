"""Expanded occupancy reference, retained from commit aff3574.

The original state/flow and binary component-event equations are deliberately
preserved here for equivalence testing. Power, ramp, reserve and cost equations
remain shared with the compact variant in configurations.py.
"""
import pulp as lp


def occupancy(model,data,specs,names,n,prefix,initial,initial_key):
    z={};flows={};components={}
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
    entered={(e['id'],j):lp.lpSum(v for (a,b,t),v in flows.items() if b==f"{e['id']}:0" and t==j) for e in data['transitions'] for j in range(n)}
    return z,entered,dict(occupancy_binary_count=len(z),transition_start_binary_count=0,
        component_event_binary_count=2*len(data['components'])*n,commitment_binary_count=len(z))

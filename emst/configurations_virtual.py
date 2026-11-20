"""Experimental virtual-configuration phase representation, with hot-only edges.

Reuse exact transition-start conservation and component event hulls. Stable-state
occupancies become continuous auxiliaries uniquely determined by binary phase
flags and transition starts. No transition-step occupancy is declared binary.
Balancing selection is an accounting selector, not physical commitment.
"""
import pulp as lp
from .configurations_compact import occupancy as compact_occupancy


def occupancy(model, data, specs, names, n, prefix, initial, initial_key):
    z, entries, counts = compact_occupancy(model, data, specs, names, n, prefix, initial, initial_key)
    operating = [c for c in data['configurations'] if c != 'OFF']
    for c in data['configurations']:
        for t in range(n):
            z[c, t].cat = lp.LpContinuous
    flags = {(c, phase, t): lp.LpVariable(f'{prefix}_virtual_{i}_{phase}_{t}', cat='Binary')
        for i, c in enumerate(operating) for phase in ('sync', 'soak', 'dispatch', 'desync') for t in range(n)}
    selected = {(c, t): lp.LpVariable(f'{prefix}_balancing_{i}_{t}', cat='Binary')
        for i, c in enumerate(operating) for t in range(n)}

    def active(edges, t):
        return lp.lpSum(z[f"{e['id']}:{step}", t] for e in edges for step in range(len(e['profile_mw'])))

    for t in range(n):
        model += lp.lpSum(selected[c, t] for c in operating) == 1
        cheapest = min(operating, key=lambda c: (data['configurations'][c]['marginal_cost'], c))
        model += selected[cheapest, t] >= z['OFF', t]
        for c in operating:
            starting = active([e for e in data['transitions'] if e['source'] == 'OFF' and e['target'] == c], t)
            stopping = active([e for e in data['transitions'] if e['source'] == c and e['target'] == 'OFF'], t)
            changing = active([e for e in data['transitions'] if e['source'] != 'OFF' and e['target'] == c], t)
            # No separate zero-output sync stage is declared for this hot-only
            # prototype. OFF-start profiles occupy soak; shutdown occupies desync.
            model += flags[c, 'sync', t] == 0
            model += flags[c, 'soak', t] == starting
            model += flags[c, 'desync', t] == stopping
            model += flags[c, 'dispatch', t] == z[c, t] + changing
            model += lp.lpSum(flags[c, phase, t] for phase in ('sync', 'soak', 'dispatch', 'desync')) <= selected[c, t]
        model += z['OFF', t] == 1 - lp.lpSum(
            flags[c, phase, t] for c in operating for phase in ('sync', 'soak', 'dispatch', 'desync'))
    model._emst_virtual_flags = getattr(model, '_emst_virtual_flags', {})
    model._emst_virtual_flags[prefix] = dict(phases=flags, balancing=selected)
    # q is now continuous; x and the explicit phase/selection indicators are binary.
    return z, entries, dict(occupancy_binary_count=0,
        transition_start_binary_count=counts['transition_start_binary_count'], component_event_binary_count=0,
        phase_binary_count=len(flags), balancing_selection_binary_count=len(selected),
        commitment_binary_count=len(flags) + len(selected) + len(entries))

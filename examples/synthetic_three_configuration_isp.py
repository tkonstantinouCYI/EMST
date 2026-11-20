"""Synthetic hot-only GT/GT_ST/2GT_ST plant for initial-ISP timing experiments.

All costs, MW limits and transition durations below are assumptions, not measured
Vasilikos data. GT slots are canonical: the lead GT remains on when adding the
second GT. This is not a model of arbitrary swapping of identical GT histories.
"""
from emst import Market, PlantState
from emst.configurations import boundary
from emst.model import PRODUCTS
from examples import synthetic_handoff_isp as handoff


def plant(transition_reserves=False):
    m = Market()
    m.add_participant('CC_owner')
    components = {c: dict(minimum_up_minutes=360, minimum_down_minutes=360)
        for c in ('lead_GT', 'extra_GT', 'ST')}
    def config(members, minimum, maximum, cost):
        return dict(components=members, minimum_mw=minimum, maximum_mw=maximum,
            marginal_cost=cost, no_load_cost_per_hour=0, ramp_mw_hour=400,
            reserve_limit_mw=.3 * maximum)
    configurations = dict(OFF=config([], 0, 0, 0), GT=config(['lead_GT'], 20, 75, 140),
        GT_ST=config(['lead_GT', 'ST'], 40, 110, 110),
        TWO_GT_ST=config(['lead_GT', 'extra_GT', 'ST'], 80, 220, 105))
    configurations['OFF']['ramp_mw_hour'] = 1
    transitions = []
    for source in ('GT', 'GT_ST', 'TWO_GT_ST'):
        for target in ('GT', 'GT_ST', 'TWO_GT_ST'):
            if source == target:
                continue
            adding = len(configurations[target]['components']) > len(configurations[source]['components'])
            steps = 13 if adding else 1
            start = configurations[source]['minimum_mw']
            finish = configurations[target]['minimum_mw']
            transitions.append(dict(id=source + '_to_' + target, source=source, target=target,
                profile_mw=[start + (finish-start)*(i+1)/steps for i in range(steps)],
                components_by_step=[configurations[target]['components']] * steps,
                cost_eur=2500 if adding else 0, ramp_mw_hour=400))
    transitions.extend([
        dict(id='OFF_to_GT', source='OFF', target='GT', profile_mw=[10, 20],
            components_by_step=[['lead_GT']]*2, cost_eur=1000, ramp_mw_hour=400),
        dict(id='GT_to_OFF', source='GT', target='OFF', profile_mw=[0],
            components_by_step=[[]], cost_eur=0, ramp_mw_hour=400)])
    if transition_reserves:
        for e in transitions:
            if 'OFF' in (e['source'],e['target']):continue
            source,target=configurations[e['source']],configurations[e['target']]
            e['reserve_eligible_products']=list(PRODUCTS)
            e['reserve_minimum_mw']=[min(source['minimum_mw'],target['minimum_mw'])]*len(e['profile_mw'])
            e['reserve_maximum_mw']=[max(source['maximum_mw'],target['maximum_mw'])]*len(e['profile_mw'])
    return m.add_configured_thermal('CC', 'CC_owner', components=components,
        configurations=configurations, transitions=transitions,
        initial_state=PlantState('TWO_GT_ST', 220,
            {c: dict(on=True, duration_minutes=1440) for c in components}),
        reserve_cost=0, strategy='marginal_cost')


def build_inputs(include_cc=True, transition_reserves=False):
    k, _ = handoff.build_inputs()
    k['assets'] = [a for a in k['assets'] if 'plant_configurations' not in a.parameters]
    ids = {a.id for a in k['assets']}
    for field in ('commercial', 'states', 'mandatory_on'):
        k[field] = {a: v for a, v in k[field].items() if a in ids}
    if include_cc:
        a = plant(transition_reserves=transition_reserves)
        k['assets'].append(a)
        k['states'][a.id] = boundary(a.initial_state)
        k['commercial'][a.id] = [0.] * 48
        k['mandatory_on'][a.id] = [0] * 48
    return k

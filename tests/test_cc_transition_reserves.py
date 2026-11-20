"""Transition-capacity eligibility, endpoint headroom and ramp-response checks."""
from copy import deepcopy
from datetime import date
import unittest
from emst import PlantState, Market
from emst.calendar import day_intervals
from emst.configurations import boundary, validate
from emst.dispatch import dispatch
from emst.model import PRODUCTS
from examples.synthetic_three_configuration_isp import plant, build_inputs
from tools.hourly_isp_suggestions import hourly_copy, capture


def asset(eligible=True):
    a=plant(transition_reserves=eligible)
    data=a.parameters['plant_configurations']
    e=next(e for e in data['transitions'] if e['id']=='GT_ST_to_TWO_GT_ST')
    e['profile_mw']=[65,80]
    e['components_by_step']=[['lead_GT','extra_GT','ST']]*2
    if eligible:
        e['reserve_minimum_mw']=[40,40]
        e['reserve_maximum_mw']=[110,110]
    a.initial_state=PlantState('GT_ST',60,{c:dict(on=c!='extra_GT',duration_minutes=1440) for c in data['components']})
    return a,e


def clear(a, requests, inherited=False):
    from unittest.mock import patch
    import emst.dispatch as module
    original=module.solve
    state='GT_ST_to_TWO_GT_ST:'+('1' if inherited else '0')
    def forced(m,mip=True):
        if mip:m+=m._emst_configuration_states['CC'][state,0]==1
        return original(m,mip)
    with patch.object(module,'solve',side_effect=forced):
        return dispatch([a],{'CC':boundary(a.initial_state)},day_intervals(date(2026,2,3),30)[:1],
            [72.5 if inherited else 62.5],[0],{'CC':[0]},
            {p:[requests.get(p,0)] for p in PRODUCTS},Market().ruleset.to_dict())


class TransitionReserveTests(unittest.TestCase):
    def test_disabled_is_zero_and_product_eligibility_is_respected(self):
        for formulation in ('reference','compact','virtual'):
            a,e=asset(False);a.parameters['configuration_formulation']=formulation
            r=clear(a,{'FCR_up':2})
            self.assertTrue(all(abs(v)<1e-7 for v in r['rows'][0]['assets']['CC']['reserve_mw'].values()))
            a,e=asset(True);a.parameters['configuration_formulation']=formulation
            e['reserve_eligible_products']=['mFRR_up']
            a.parameters['plant_configurations']['configurations']['GT_ST']['reserve_limit_mw']=10
            r=clear(a,{'mFRR_up':20,'FCR_up':2})
            reserves=r['rows'][0]['assets']['CC']['reserve_mw']
            self.assertAlmostEqual(reserves['mFRR_up'],10,places=5)
            self.assertAlmostEqual(reserves['FCR_up'],0,places=5)

    def test_terminal_headroom_and_entry_footroom_not_just_average(self):
        a,e=asset(True);e['reserve_maximum_mw']=[66,110]
        r=clear(a,{'FCR_up':10})
        self.assertAlmostEqual(r['rows'][0]['assets']['CC']['reserve_mw']['FCR_up'],1,places=5)
        a,e=asset(True);e['reserve_minimum_mw']=[60,40]
        r=clear(a,{'FCR_down':10})
        self.assertAlmostEqual(r['rows'][0]['assets']['CC']['reserve_mw']['FCR_down'],0,places=5)

    def test_scheduled_ramp_consumes_response_capability(self):
        a,e=asset(True);e['ramp_mw_hour']=12
        r=clear(a,{'FCR_up':1})
        # Baseline moves 60->65 MW in .5h: 10 MW/h of 12 MW/h used.
        self.assertAlmostEqual(r['rows'][0]['assets']['CC']['reserve_mw']['FCR_up'],2*.5/60,places=5)

    def test_slow_stable_ramp_caps_reserves_without_disabling_transition(self):
        a,e=asset(True)
        a.parameters['plant_configurations']['configurations']['GT_ST']['ramp_mw_hour']=2
        # The explicit transition permits its 10 MW/h baseline ramp. A slower
        # stable-state response rate must cap reserves, not make that path infeasible.
        r=clear(a,{'FCR_up':1})
        self.assertAlmostEqual(r['rows'][0]['assets']['CC']['reserve_mw']['FCR_up'],2*.5/60,places=5)

    def test_aggregate_products_share_headroom(self):
        a,e=asset(True);e['reserve_maximum_mw']=[75,110]
        r=clear(a,{'FCR_up':2,'mFRR_up':20})
        reserves=r['rows'][0]['assets']['CC']['reserve_mw']
        self.assertAlmostEqual(sum(reserves[p] for p in ('FCR_up','aFRR_up','mFRR_up','RR_up')),10,places=5)

    def test_inherited_transition_can_provide_capacity(self):
        a,e=asset(True)
        a.initial_state=PlantState('GT_ST',65,
            {c:dict(on=True,duration_minutes=30 if c=='extra_GT' else 1440) for c in a.parameters['plant_configurations']['components']},
            dict(id=e['id'],elapsed_minutes=30))
        r=clear(a,{'mFRR_up':5},inherited=True)
        # Zero capacity bids can produce alternative optima with excess awards.
        self.assertGreaterEqual(r['rows'][0]['assets']['CC']['reserve_mw']['mFRR_up'],5-1e-6)
        self.assertLessEqual(r['rows'][0]['assets']['CC']['reserve_mw']['mFRR_up'],30+1e-6)
        self.assertAlmostEqual(r['rows'][0]['slack']['reserve_shortage_mw']['mFRR_up'],0,places=5)

    def test_validation_rejects_unknown_products_and_missing_envelopes(self):
        a=plant(True);data=a.parameters['plant_configurations']
        for changes in ({'reserve_eligible_products':['bad']},{'reserve_minimum_mw':None},
            {'reserve_maximum_mw':[1]},{'reserve_eligible_products':['FCR_up','FCR_up']}):
            edges=deepcopy(data['transitions']);edges[0].update(changes)
            with self.assertRaises(ValueError):
                validate(data['components'],data['configurations'],edges,a.initial_state)

    def test_hourly_conversion_and_model_size_add_no_variables(self):
        sizes=[]
        for enabled in (False,True):
            k,audit,responses=hourly_copy(build_inputs(True,transition_reserves=enabled),configuration_formulation='virtual')
            a=next(a for a in k['assets'] if a.id=='CC')
            for e in a.parameters['plant_configurations']['transitions']:
                if e.get('reserve_eligible_products'):
                    self.assertEqual(len(e['profile_mw']),len(e['reserve_maximum_mw']))
            m=capture(k,responses)['model']
            sizes.append((len(m.variables()),sum(v.isBinary() for v in m.variables()),len(m.constraints)))
        self.assertEqual(sizes[0][:2],sizes[1][:2])
        self.assertEqual(sizes[1][2]-sizes[0][2],11*24)

    def test_public_day_with_transition_capacity_clears_and_settles(self):
        from examples.configured_plant import build_market
        m=build_market(configuration_formulation='compact',transition_reserves=True)
        m.assets['ccgt'].parameters['reserve_prices']={p:.1 for p in PRODUCTS}
        history={t:[[60.]*6 for _ in range(3)] for t in range(48)}
        result=m.run(time_index=m.time_index('2026-11-05'),rtbm_interval_minutes=30,
            reserve_requirements={'FCR':{'up':[1.]*48,'down':[1.]*48}},historical_rtbm_prices=history)
        self.assertTrue(result.audit['passed'])
        self.assertTrue(result.manifest['physical_security_feasible'])
        transitions=[row['assets']['ccgt'] for row in result.isp_runs[0]['rows'] if row['assets']['ccgt']['configuration_transition']]
        self.assertTrue(transitions)
        self.assertTrue(any(row['reserve_mw']['FCR_up']>1e-6 for row in transitions))
        self.assertTrue(all(owner['reconciled'] for owner in result.economics['participants'].values()))
        self.assertEqual(sum(meter['asset']=='ccgt' for meter in result.meters),48)
        self.assertAlmostEqual(result.economics['assets']['ccgt']['physical_costs']['totals']['transition_cost_eur'],250)


if __name__=='__main__':unittest.main()

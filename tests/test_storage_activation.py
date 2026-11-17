from datetime import date
from unittest.mock import patch
import unittest
from emst import Market,StorageState,Strategy
from emst.calendar import day_intervals
from emst.dispatch import dispatch
from emst.model import PRODUCTS
from emst.solver import SolveError


def storage_market(enabled=True,two=False):
    m=Market({'storage':{'participation_balancing_energy':enabled}})
    for aid in ('a','b') if two else ('a',):
        m.add_participant(aid)
        m.add_storage(aid,aid,power_mw=10,capacity_mwh=100,initial_state=StorageState(0,50),
            efficiency_charge=.9,efficiency_discharge=.8,
            strategy_parameters={'balancing_up_price':55.,'balancing_down_price':55.})
    return m


def clear(m,rtbm,q,energy):
    interval=day_intervals(date(2026,11,5),5 if rtbm else 30)[0]
    return dispatch(list(m.assets.values()),{a.id:{'power_mw':0.,'soc_mwh':50.} for a in m.assets.values()},
        [interval],[10+energy/interval.hours],[10.],{a.id:[q] for a in m.assets.values()},
        {p:[0.] for p in PRODUCTS},m.ruleset.to_dict(),rtbm=rtbm)


class StorageActivationTests(unittest.TestCase):
    def test_equal_prices_direction_soc_and_schedule_reversal(self):
        for rtbm in (False,True):
            dt=1/12 if rtbm else .5
            for q,energy in ((-.2,-.1),(.2,.1),(0,0),(-.2,.1),(-10*dt,10*dt),(10*dt,-10*dt)):
                with self.subTest(rtbm=rtbm,schedule=q,actual=energy):
                    result=clear(storage_market(),rtbm,q,energy)
                    row=result['rows'][0]['assets']['a']
                    self.assertAlmostEqual(row['energy_mwh'],energy,places=6)
                    self.assertAlmostEqual(row['up_mwh'],max(energy-q,0),places=6)
                    self.assertAlmostEqual(row['down_mwh'],max(q-energy,0),places=6)
                    self.assertLessEqual(min(row['up_mwh'],row['down_mwh']),1e-7)
                    self.assertLessEqual(min(row['charge_mwh'],row['discharge_mwh']),1e-7)
                    expected=50+max(-energy,0)*.9-max(energy,0)/.8
                    self.assertAlmostEqual(row['soc_mwh'],expected,places=6)

    def test_forced_offsetting_activations_are_infeasible(self):
        import emst.dispatch as module
        original=module.solve
        def force(problem,mip=True):
            if mip:
                variables={v.name:v for v in problem.variables()}
                problem+=variables['storage_up_0_0']>=.1
                problem+=variables['storage_down_0_0']>=.1
            return original(problem,mip)
        for rtbm in (False,True):
            with self.subTest(rtbm=rtbm),patch.object(module,'solve',side_effect=force):
                with self.assertRaises(SolveError) as error:clear(storage_market(),rtbm,0,0)
                self.assertEqual(error.exception.record['termination_reason'],'infeasible')

    def test_different_assets_can_activate_opposite_directions(self):
        import emst.dispatch as module
        original=module.solve
        def force(problem,mip=True):
            if mip:
                variables={v.name:v for v in problem.variables()}
                problem+=variables['storage_up_0_0']>=.1
                problem+=variables['storage_down_1_0']>=.1
            return original(problem,mip)
        for rtbm in (False,True):
            with self.subTest(rtbm=rtbm),patch.object(module,'solve',side_effect=force):
                result=clear(storage_market(two=True),rtbm,0,0)
                a=result['rows'][0]['assets']['a'];b=result['rows'][0]['assets']['b']
                self.assertGreaterEqual(a['up_mwh'],.1-1e-7)
                self.assertGreaterEqual(b['down_mwh'],.1-1e-7)
                self.assertLessEqual(min(a['up_mwh'],a['down_mwh']),1e-7)
                self.assertLessEqual(min(b['up_mwh'],b['down_mwh']),1e-7)

    def test_disabled_energy_needs_no_activation_binary(self):
        for rtbm in (False,True):
            r=clear(storage_market(enabled=False),rtbm,0,0)
            self.assertEqual(r['storage_activation_binary_count'],0)
            self.assertEqual(r['rows'][0]['assets']['a']['up_mwh'],0)
            self.assertEqual(r['rows'][0]['assets']['a']['down_mwh'],0)

    def test_zero_schedule_reuses_physical_binary(self):
        for rtbm in (False,True):
            zero=clear(storage_market(),rtbm,0,.1)
            nonzero=clear(storage_market(),rtbm,.2,.1)
            self.assertEqual(zero['storage_activation_binary_count'],0)
            self.assertEqual(nonzero['storage_activation_binary_count'],1)
            row=zero['rows'][0]['assets']['a']
            self.assertTrue(row['activation_mode_reused'])
            self.assertEqual(row['activation_up_mode'],1-row['physical_charging_mode'])
            self.assertFalse(nonzero['rows'][0]['assets']['a']['activation_mode_reused'])

    def test_directional_offer_cost_matches_both_directions(self):
        for rtbm in (False,True):
            for q,energy in ((-.2,-.1),(.2,.1),(0,0)):
                m=storage_market()
                m.assets['a'].strategy=Strategy('fixed_circular',{'balancing_up_price':60.,'balancing_down_price':40.})
                r=clear(m,rtbm,q,energy)
                expected=60*max(energy-q,0)-40*max(q-energy,0)
                self.assertAlmostEqual(r['milp_objective_eur'],expected,places=5)
                self.assertAlmostEqual(r['fixed_lp_objective_eur'],expected,places=5)
        m.assets['a'].strategy=Strategy('fixed_circular',{'balancing_up_price':40.,'balancing_down_price':60.})
        with self.assertRaises(ValueError):clear(m,True,0,0)

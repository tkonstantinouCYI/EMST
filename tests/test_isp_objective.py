"""Hand-calculated ISP objective checks, independent of dispatch internals."""
import unittest
from datetime import date,datetime,timezone
from emst import Market,ThermalState,StorageState
from emst.calendar import day_intervals
from emst.model import PRODUCTS,Unit
from emst.dispatch import dispatch
from emst.isp import schedule


class ISPOfferObjectiveTests(unittest.TestCase):
    def test_downward_credit_and_each_directional_capacity_quote(self):
        market=Market({'schedule_deviation_cost_eur_mwh':999})
        market.add_participant('p')
        quotes=dict(zip(PRODUCTS,[11,12,13,14,15,16,17]))
        asset=market.add_thermal('u','p',capacity_mw=100,minimum_mw=0,marginal_cost=50,
            initial_state=ThermalState('online',40,180),ramp_mw_hour=600,
            no_load_cost_per_hour=999,startup_cost=7,energy_up_price=120,energy_down_price=90,
            reserve_prices=quotes)
        periods=day_intervals(date(2026,11,5),30)[:1]
        requirements={p:[i+1 if i<6 else 0] for i,p in enumerate(PRODUCTS)}
        public=dispatch([asset],{'u':dict(on=1,stage='online',power_mw=40,duration_minutes=180)},
            periods,[30],[0],{'u':[20]},requirements,market.ruleset.to_dict())
        unit=Unit('u','p',100,0,50,600,40,1,6,no_load_eur_period=999,
            reserve_eur_mw_period=quotes,energy_up_eur_mwh=120,energy_down_eur_mwh=90)
        fixture=schedule([unit],periods,[30],[0],{'u':[20]},requirements,
            {'u':dict(on=1,end_mw=40,duration_periods=6)},datetime(2026,11,4,tzinfo=timezone.utc),'offer_objective')
        # 15 MWh delivery vs 20 MWh schedule: downward credit 5*90=450.
        # Capacity: 1*11+2*12+3*13+4*14+5*15+6*16=301 EUR.
        for result in (public,fixture):
            costs=result['objective_components_eur']
            self.assertAlmostEqual(costs['energy_up_cost'],0,places=5)
            self.assertAlmostEqual(costs['energy_down_credit'],450,places=5)
            self.assertAlmostEqual(costs['reserve_capacity_cost'],301,places=5)
            self.assertAlmostEqual(costs['startup_cost'],0,places=5)
            self.assertAlmostEqual(costs['total'],-149,places=5)
            self.assertAlmostEqual(result['fixed_lp_objective_eur'],-149,places=5)

    def test_upward_energy_plus_startup_only(self):
        market=Market();market.add_participant('p')
        asset=market.add_thermal('u','p',capacity_mw=100,minimum_mw=0,marginal_cost=50,
            initial_state=ThermalState('offline',0,600),energy_up_price=120,energy_down_price=90,
            startup_cost=7,no_load_cost_per_hour=999)
        result=dispatch([asset],{'u':dict(on=0,stage='offline',power_mw=0,duration_minutes=600)},
            day_intervals(date(2026,11,5),30)[:1],[10],[0],{'u':[0]},
            {p:[0] for p in PRODUCTS},market.ruleset.to_dict())
        self.assertAlmostEqual(result['objective_components_eur']['energy_up_cost'],600)
        self.assertAlmostEqual(result['objective_components_eur']['startup_cost'],7)
        self.assertAlmostEqual(result['fixed_lp_objective_eur'],607)

    def test_storage_degradation_is_excluded_from_ISP_objective(self):
        market=Market({'storage':{'participation_balancing_energy':True}})
        market.add_participant('p')
        thermal=market.add_thermal('u','p',capacity_mw=50,minimum_mw=0,marginal_cost=145,
            initial_state=ThermalState('online',20,180))
        storage=market.add_storage('s','p',power_mw=10,capacity_mwh=40,
            initial_state=StorageState(0,20),degradation_cost=999,
            strategy_parameters={'balancing_up_price':50,'balancing_down_price':0})
        result=dispatch([thermal,storage],{'u':dict(on=1,stage='online',power_mw=20,duration_minutes=180),
            's':dict(power_mw=0,soc_mwh=20)},day_intervals(date(2026,11,5),30)[:1],
            [20],[0],{'u':[10],'s':[0]},{p:[0] for p in PRODUCTS},market.ruleset.to_dict())
        self.assertAlmostEqual(result['fixed_lp_objective_eur'],5*50-5*145)

    def test_invalid_energy_price_pair_rejected(self):
        market=Market();market.add_participant('p')
        with self.assertRaises(ValueError):
            market.add_thermal('u','p',capacity_mw=100,minimum_mw=0,marginal_cost=50,
                initial_state=ThermalState('online',20,180),energy_up_price=40,energy_down_price=60)


if __name__=='__main__':unittest.main()

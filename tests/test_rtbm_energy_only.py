"""RTBM can consume the physical room that ISP procured as reserves."""
import unittest
from datetime import date
from unittest.mock import patch
from emst import Market,ThermalState,StorageState
from emst.model import PRODUCTS,Unit
from emst.calendar import day_intervals
from emst.dispatch import dispatch
from emst.rtbm import clear_rtu
from emst.optimization import solve


class RTBMEnergyOnlyTests(unittest.TestCase):
    def test_thermal_uses_reserved_room_up_to_physical_limits(self):
        interval=day_intervals(date(2026,11,5),5)[0]
        for prior,demand,direction,endpoint in ((80,99,'up',100),(40,21,'down',20)):
            with self.subTest(direction=direction):
                market=Market();market.add_participant('p')
                a=market.add_thermal('u','p',capacity_mw=100,minimum_mw=20,marginal_cost=50,
                    initial_state=ThermalState('dispatch',prior,600),ramp_mw_hour=12000)
                awards={p:10 if p in ('FCR_'+direction,'aFRR_'+direction) else 0 for p in PRODUCTS}
                reference={'u':dict(on=1,phase='dispatch',agc=1,reserve_mw=awards)}
                captured=[]
                def checked(model,*args,**kwargs):
                    captured.append(([v.name for v in model.variables()],list(model.constraints)))
                    return solve(model,*args,**kwargs)
                with patch('emst.dispatch.solve',side_effect=checked):
                    r=dispatch([a],{'u':dict(on=1,stage='dispatch',power_mw=prior,duration_minutes=600)},
                        [interval],[demand],[0],{'u':[prior/12]},
                        {p:[9999] for p in PRODUCTS},market.ruleset.to_dict(),
                        commitment={'u':1},reference=reference,rtbm=True)
                row=r['rows'][0]['assets']['u']
                self.assertAlmostEqual(row['power_mw'],demand,places=5)
                self.assertAlmostEqual(row['end_mw'],endpoint,places=5)
                self.assertAlmostEqual(r['rows'][0]['slack']['energy_shortage_mwh'],0,places=5)
                self.assertEqual(row['reserve_mw'],{})
                self.assertEqual(r['rows'][0]['reserve_prices'],{})
                self.assertEqual(r['rows'][0]['slack']['reserve_shortage_mw'],{})
                self.assertEqual(r['capacity_products'],[])
                self.assertEqual(r['requirements_mw'],{})
                self.assertTrue(all(not any('reserve' in v or 'capacity' in v for v in variables) for variables,_ in captured))
                self.assertTrue(all(not any(name.startswith('requirement_') for name in constraints) for _,constraints in captured))
                unit=Unit('u','p',100,20,50,12000,prior,1,20)
                legacy=clear_rtu(interval,[unit],{'u':dict(on=1,end_mw=prior,duration_rtus=120)},reference,
                    {'u':prior/12},demand,0,demand,0,delivery_equals_dispatch=True,
                    reserve_requirements={p:9999 for p in PRODUCTS})
                self.assertAlmostEqual(legacy['rows'][0]['instructed_energy_mwh'],demand/12,places=5)
                self.assertAlmostEqual(legacy['rows'][0]['actual_terminal_mw'],endpoint,places=4)
                self.assertEqual(legacy['capacity_products'],[])
                self.assertEqual(legacy['slack']['reserve_shortage_mw'],{})

    def test_storage_can_use_reserved_power_and_energy_inventory(self):
        market=Market({'storage':{'participation_balancing_energy':True,'participation_balancing_capacity':True}})
        market.add_participant('p')
        a=market.add_storage('s','p',power_mw=10,capacity_mwh=40,efficiency_charge=1,efficiency_discharge=1,
            initial_state=StorageState(0,3))
        interval=day_intervals(date(2026,11,5),5)[0]
        rules=market.ruleset.to_dict();rules['reserve_sustain_hours']=1000
        reference={'s':dict(reserve_mw={p:10 if p=='FCR_up' else 0 for p in PRODUCTS},soc_mwh=3)}
        result=dispatch([a],{'s':dict(power_mw=0,soc_mwh=3)},[interval],[10],[0],{'s':[0]},
            {p:[10] for p in PRODUCTS},rules,reference=reference,rtbm=True)
        row=result['rows'][0]['assets']['s']
        self.assertAlmostEqual(row['power_mw'],10)
        self.assertAlmostEqual(row['soc_mwh'],3-10/12)
        self.assertLess(row['soc_mwh'],2.5)  # It may consume the old 15-minute reserve buffer.
        self.assertEqual(row['reserve_mw'],{})
        self.assertEqual(result['rows'][0]['slack']['reserve_shortage_mw'],{})


if __name__=='__main__':unittest.main()

import unittest
from dataclasses import asdict
from copy import deepcopy
from datetime import timedelta
from emst import Market,Ruleset,Strategy,ThermalState,StorageState
from emst.model import PRODUCTS
from emst.dispatch import dispatch
from emst.calendar import day_intervals
from emst.series import mw_values
from emst.optimization import highspy


def example(actual_demand=101,actual_res=19):
    m=Market()
    for id in ('producer','consumer','battery_owner','solar_owner','balance_party'):m.add_participant(id,brp='balance_party' if id=='solar_owner' else None)
    m.add_thermal('unit_blue','producer',capacity_mw=200,minimum_mw=20,marginal_cost=65,
        initial_state=ThermalState('online',80,480))
    m.add_supplier('factory_load','consumer',demand_forecast=[100]*48,actual_consumption=[actual_demand]*48)
    m.add_res('solar_east','solar_owner',generation_forecast=[20]*48,actual_generation=[actual_res]*48)
    m.add_storage('battery_west','battery_owner',power_mw=10,capacity_mwh=40,initial_state=StorageState(0,20),
        strategy_parameters=dict(buy_mwh=[2 if t in (10,11) else 0 for t in range(48)],sell_mwh=[1.805 if t in (34,35) else 0 for t in range(48)]))
    return m


HISTORY={t:[[80.]*6 for _ in range(3)] for t in range(48)}


class PublicValidationTests(unittest.TestCase):
    def test_rules_are_copied_and_no_series_or_unsupported_features(self):
        config={'storage':{'participation_balancing_energy':True}}
        r=Ruleset(config);config['storage']['participation_balancing_energy']=False
        self.assertTrue(r['storage']['participation_balancing_energy'])
        value=r.to_dict();value['storage']['participation_balancing_energy']=False
        self.assertTrue(r['storage']['participation_balancing_energy'])
        self.assertFalse(r['storage']['participation_balancing_capacity'])
        for config in ({'demand':[1]*48},{'storage':{'participation_balancing_capacity':[True]}}):
            with self.assertRaises((TypeError,ValueError)):Ruleset(config)
        with self.assertRaises(NotImplementedError):Ruleset({'interconnections':{'enabled':True}})
        with self.assertRaises(NotImplementedError):Ruleset({'markets':{'intraday_auctions':True}})

    def test_roles_initial_states_and_strategy_eligibility(self):
        m=Market();m.add_participant('p')
        with self.assertRaises(ValueError):m.add_participant('p')
        with self.assertRaises(ValueError):m.add_thermal('x','p',capacity_mw=100,minimum_mw=20,marginal_cost=50,initial_state=ThermalState('offline',10,20))
        with self.assertRaises(ValueError):m.add_storage('s','p',power_mw=10,capacity_mwh=20,initial_state=StorageState(0,21))
        with self.assertRaises(ValueError):m.add_storage('s','p',power_mw=10,capacity_mwh=20,initial_state=StorageState(float('nan'),10))
        with self.assertRaises(ValueError):m.add_storage('s','p',power_mw=10,capacity_mwh=20,initial_state=StorageState(0,10),strategy='marginal_cost')
        with self.assertRaises(ValueError):m.add_supplier('d','unknown',demand_forecast=[1]*48,actual_consumption=[1]*48)
        self.assertIn('forecast_arbitrage',m.available_strategies()['storage'])

    def test_participant_strategy_defaults_and_asset_override(self):
        m=Market();m.add_participant('p',strategies={'thermal':Strategy('fixed_markup',{'markup_eur_mwh':10})})
        a=m.add_thermal('a','p',capacity_mw=100,minimum_mw=0,marginal_cost=40,initial_state=ThermalState('online',20,120))
        m.add_participant('q',strategies={'thermal':Strategy('fixed_markup',{'markup_eur_mwh':10})})
        b=m.add_thermal('b','q',capacity_mw=100,minimum_mw=0,marginal_cost=40,initial_state=ThermalState('online',20,120),strategy='marginal_cost')
        self.assertEqual(a.strategy.price('thermal',40,m.ruleset.to_dict()),50)
        self.assertEqual(b.strategy.price('thermal',40,m.ruleset.to_dict()),40)

    def test_arrays_and_timestamp_validation(self):
        index=Market.time_index('2026-11-05')
        self.assertEqual(mw_values([2]*48,index,'forecast'),[2.]*48)
        for data in ([1]*47,[float('nan')]*48,[-1]*48):
            with self.assertRaises(ValueError):mw_values(data,index,'forecast')
        with self.assertRaises(ValueError):Market().run(time_index=[t.replace(tzinfo=None) for t in index])

    def test_indexed_dataframe_input_when_available(self):
        try:import pandas as pd
        except ImportError:self.skipTest('Optional pandas extra not installed')
        index=Market.time_index('2026-11-05');frame=pd.DataFrame({'MW':[3.]*48},index=index)
        self.assertEqual(mw_values(frame,index,'frame'),[3.]*48)
        with self.assertRaises(ValueError):mw_values(frame.set_axis([t+timedelta(minutes=5) for t in index]),index,'frame')
        with self.assertRaises(ValueError):mw_values(frame.assign(other=1),index,'frame')


class StoragePermissionTests(unittest.TestCase):
    def test_previous_day_stage_and_duration_constrain_first_intervals(self):
        from datetime import date
        intervals=day_intervals(date(2026,11,5),30)[:3]
        for stage,power,age,startup,shutdown in (('offline',0,10,0,0),('startup',5,30,60,0),('shutdown',10,30,0,60)):
            m=Market();m.add_participant('p')
            m.add_thermal('carried','p',capacity_mw=100,minimum_mw=20,marginal_cost=30,
                initial_state=ThermalState(stage,power,age),startup_minutes=startup,shutdown_minutes=shutdown)
            m.add_participant('backup_owner')
            m.add_thermal('backup','backup_owner',capacity_mw=100,minimum_mw=0,marginal_cost=100,
                initial_state=ThermalState('online',40,180))
            state={'carried':dict(stage=stage,on=int(stage!='offline'),power_mw=power,duration_minutes=age),
                   'backup':dict(stage='online',on=1,power_mw=40,duration_minutes=180)}
            r=dispatch(list(m.assets.values()),state,intervals,[40]*3,[0]*3,{'carried':[0]*3,'backup':[20]*3},
                {p:[0]*3 for p in PRODUCTS},m.ruleset.to_dict())
            row=r['rows'][0]['assets']['carried']
            if stage=='offline':
                self.assertEqual([q['assets']['carried']['on'] for q in r['rows'][:2]],[0,0])
            elif stage=='startup':
                self.assertNotIn('end_mw',row)
                self.assertAlmostEqual(row['energy_mwh'],.5*(5+20)*.5)
            else:
                self.assertNotIn('end_mw',row)
                self.assertAlmostEqual(row['energy_mwh'],.5*(10+0)*.5)

    def setup_case(self,energy,capacity):
        m=Market({'storage':{'participation_balancing_energy':energy,'participation_balancing_capacity':capacity}})
        m.add_participant('p');m.add_participant('b')
        m.add_thermal('unit','p',capacity_mw=50,minimum_mw=0,marginal_cost=145,reserve_cost=10,
            initial_state=ThermalState('online',20,180))
        m.add_storage('store','b',power_mw=10,capacity_mwh=40,reserve_cost=1,initial_state=StorageState(0,20))
        state={'unit':dict(stage='online',on=1,power_mw=20,duration_minutes=180),'store':dict(power_mw=0,soc_mwh=20)}
        return m,state

    def test_independent_energy_and_capacity_flags(self):
        intervals=day_intervals(__import__('datetime').date(2026,11,5),30)[:2]
        for energy,capacity in ((False,False),(True,False),(False,True),(True,True)):
            with self.subTest(energy=energy,capacity=capacity):
                m,state=self.setup_case(energy,capacity)
                req={p:[3.,3.] if p=='FCR_up' else [0.,0.] for p in PRODUCTS}
                r=dispatch(list(m.assets.values()),state,intervals,[20,20],[0,0],{'unit':[10,10],'store':[0,0]},req,m.ruleset.to_dict())
                for row in r['rows']:
                    store=row['assets']['store']
                    if not energy:self.assertAlmostEqual(store['energy_mwh'],0,places=6)
                    else:self.assertGreater(store['energy_mwh'],0)
                    if not capacity:self.assertTrue(all(abs(v)<1e-6 for v in store['reserve_mw'].values()))
                    elif not energy:self.assertAlmostEqual(store['reserve_mw']['FCR_up'],3,places=6)
                    self.assertGreaterEqual(store['soc_mwh'],0)

    def test_upward_can_reduce_charge_and_downward_can_charge(self):
        intervals=day_intervals(__import__('datetime').date(2026,11,5),30)[:1]
        m,state=self.setup_case(True,False)
        req={p:[0.] for p in PRODUCTS}
        r=dispatch(list(m.assets.values()),state,intervals,[20],[0],{'unit':[12],'store':[-2]},req,m.ruleset.to_dict())
        self.assertGreater(r['rows'][0]['assets']['store']['up_mwh'],0)
        m.assets['store'].strategy=Strategy('fixed_circular',{'balancing_up_price':300,'balancing_down_price':200})
        r=dispatch(list(m.assets.values()),state,intervals,[20],[0],{'unit':[10],'store':[0]},req,m.ruleset.to_dict())
        self.assertGreater(r['rows'][0]['assets']['store']['charge_mwh'],0)
        self.assertGreater(r['rows'][0]['assets']['store']['down_mwh'],0)

    def test_optional_ranges_keep_public_shadow_prices(self):
        from datetime import date
        m,state=self.setup_case(False,True);intervals=day_intervals(date(2026,11,5),30)[:1]
        req={p:[3.] if p=='FCR_up' else [0.] for p in PRODUCTS}
        args=(list(m.assets.values()),state,intervals,[20],[0],{'unit':[10],'store':[0]},req,m.ruleset.to_dict())
        normal=dispatch(*args);diagnosed=dispatch(*args,price_ranges=True)
        self.assertEqual(normal['rows'][0]['reserve_prices'],diagnosed['rows'][0]['reserve_prices'])
        self.assertEqual(len(diagnosed['reserve_dual_ranges']),1)


@unittest.skipUnless(highspy is not None,'Full generic-day integration uses the HiGHS audit extra; dispatch primitives are tested with both backends')
class PublicDayTests(unittest.TestCase):
    def test_enabled_storage_energy_and_capacity_full_day(self):
        import runpy
        from pathlib import Path
        example_file=Path(__file__).resolve().parents[1]/'examples/library_day.py'
        build=runpy.run_path(str(example_file),run_name='test_storage_example')['build_market']
        m,index,req,history=build(storage_energy=True,storage_capacity=True)
        a=m.assets['battery'];a.strategy=Strategy('fixed_circular',{**a.strategy.parameters,'balancing_up_price':50})
        r=m.run(time_index=index,reserve_requirements=req,historical_rtbm_prices=history)
        self.assertTrue(r.audit['passed'],r.audit)
        self.assertGreater(sum(x['assets']['battery']['up_mwh']+x['assets']['battery']['down_mwh'] for x in r.rtbm),0)
        self.assertTrue(any(e['component'] in ('S05','S06','S07') and e['account']=='participant:storage_owner:nonDAM' and float(e['cash_eur'])>0 for e in r.ledger))
        self.assertTrue(all(0<=x['assets']['battery']['soc_mwh']<=40 for x in r.rtbm))

    def test_arbitrary_names_exact_delivery_and_no_future_actual_leak(self):
        a=example();b=example(100,20)
        a.assets['factory_load'].actual=[101.]*288
        kwargs=dict(time_index=Market.time_index('2026-11-05'),reserve_requirements={'FCR_up':[1]*48},historical_rtbm_prices=HISTORY)
        r=a.run(**kwargs);other=b.run(**kwargs)
        self.assertTrue(r.audit['passed'],r.audit)
        self.assertEqual(r.dam,other.dam)
        self.assertEqual({a:{k:v for k,v in row.items() if k!='actual_mwh'} for a,row in r.rtbm[0]['assets'].items()},
                         {a:{k:v for k,v in row.items() if k!='actual_mwh'} for a,row in other.rtbm[0]['assets'].items()})
        self.assertAlmostEqual(r.rtbm[1]['system_prediction']['net_demand_prediction_mw'],82)
        self.assertAlmostEqual(other.rtbm[1]['system_prediction']['net_demand_prediction_mw'],80)
        self.assertAlmostEqual(r.manifest['physical_imbalance_mwh'],2/12,places=5)
        self.assertAlmostEqual(other.manifest['physical_imbalance_mwh'],0)
        self.assertAlmostEqual(r.ending_states['battery_west']['soc_mwh'],20)
        self.assertEqual(r.ending_states['unit_blue']['time_in_stage_minutes'],1920)
        self.assertTrue(any(e['component']=='S04' and e['account']=='participant:balance_party:nonDAM' and float(e['cash_eur'])!=0 for e in r.ledger))
        self.assertFalse(any(e['component'] in ('S05','S06','S07','S08') and e['account']=='participant:battery_owner:nonDAM' for e in r.ledger))
        self.assertEqual(r.isp_runs[1]['first_period'],24)
        self.assertEqual(len(r.isp_runs[1]['forecast_bridge']),12)

    def test_market_price_forecast_storage_arbitrage_no_storage_forecast(self):
        from emst.market_engine import _storage_profile
        m=Market();m.add_participant('p')
        a=m.add_storage('s','p',power_mw=10,capacity_mwh=40,initial_state=StorageState(0,20),strategy='forecast_arbitrage')
        prices=[10.]*24+[100.]*24
        buy,sell=_storage_profile(a,Market.time_index('2026-11-05'),prices,m.ruleset.to_dict())
        self.assertGreater(sum(buy),0);self.assertGreater(sum(sell),0)
        self.assertGreater(sum(p*(d-c) for p,c,d in zip(prices,buy,sell)),0)
        self.assertAlmostEqual(20+sum(buy)*.95-sum(sell)/.95,20,places=5)
        with self.assertRaises(ValueError):_storage_profile(a,Market.time_index('2026-11-05'),None,m.ruleset.to_dict())


if __name__=='__main__':unittest.main()

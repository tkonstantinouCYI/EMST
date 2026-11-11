from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from emst import Market,ThermalState,StorageState
from emst.multiday import load_checkpoint
from emst.optimization import highspy


TEMP_ROOT=Path(__file__).resolve().parents[1]/'tmp'
TEMP_ROOT.mkdir(exist_ok=True)


def market():
    m=Market()
    for p in ('generator','supplier','battery'):m.add_participant(p)
    m.add_thermal('unit','generator',capacity_mw=100,minimum_mw=10,marginal_cost=60,
        initial_state=ThermalState('dispatch',20,480))
    m.add_supplier('load','supplier',demand_forecast=[20]*48,actual_consumption=[22]*48)
    m.add_storage('store','battery',power_mw=5,capacity_mwh=10,initial_state=StorageState(0,5),
        strategy_parameters={'buy_mwh':[1 if t==10 else 0 for t in range(48)],
                             'sell_mwh':[.8 if t==30 else 0 for t in range(48)]})
    return m


def scenario():
    return {day:dict(forecasts={'load':[20+offset]*48},actuals={'load':[22+offset]*48},
        historical_rtbm_prices={t:[[80]*6 for _ in range(3)] for t in range(48)})
        for day,offset in (('2026-11-05',0),('2026-11-06',1))}


class MultiDayValidationTests(unittest.TestCase):
    def test_missing_inputs_fail_before_execution(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as d:
            with self.assertRaisesRegex(ValueError,'Missing scenario'):
                market().run_period(start='2026-11-05',end='2026-11-06',scenario={},checkpoint_directory=d)
            self.assertFalse((Path(d)/'checkpoint.json').exists())


@unittest.skipUnless(highspy is not None,'Full multi-day integration requires HiGHS')
class MultiDayIntegrationTests(unittest.TestCase):
    def test_two_days_equal_checkpoint_resume(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'tmp') as directory:
            root=Path(directory);m=market();initial=deepcopy(m.assets['unit'].initial_state)
            inputs=scenario()
            # Day two must obtain its historical prices from persisted history/bootstrap.
            inputs['2026-11-06'].pop('historical_rtbm_prices')
            continuous=m.run_period(start='2026-11-05',end='2026-11-06',scenario=inputs,
                checkpoint_directory=root/'continuous')
            first=m.run_period(start='2026-11-05',end='2026-11-05',scenario=inputs,
                checkpoint_directory=root/'split')
            resumed=market().run_period(start='2026-11-06',end='2026-11-06',scenario=inputs,
                checkpoint_directory=root/'split',resume_from=first.checkpoint)
            self.assertEqual(continuous.completed_days,resumed.completed_days)
            for day in continuous.days:
                # JSON normalises integer-keyed tables as in saved results.
                left=json.loads(json.dumps(asdict(continuous.days[day])))
                right=json.loads(json.dumps(asdict(resumed.days[day])))
                self.assertEqual(left,right)
                self.assertTrue(continuous.days[day].audit['passed'])
            one,two=continuous.days.values()
            starts={a['id']:a['initial_state'] for a in two.inputs['assets']}
            self.assertEqual(starts,one.ending_states | {'load':None})
            self.assertNotEqual(one.ending_states['store']['soc_mwh'],5)
            self.assertGreater(one.ending_states['unit']['time_in_stage_minutes'],480)
            self.assertEqual(two.inputs['previous_system_observation'],one.rtbm[-1]['system_observation'])
            self.assertEqual(m.assets['unit'].initial_state,initial)
            self.assertEqual(m.assets['store'].initial_state.soc_mwh,5)
            state,_=load_checkpoint(resumed.checkpoint,market())
            self.assertEqual(len(state['price_history']),2)
            self.assertFalse(state['next_day_lookahead'])
            with self.assertRaisesRegex(ValueError,'immediately follow'):
                m.run_period(start='2026-11-06',end='2026-11-06',scenario=inputs,
                    checkpoint_directory=root/'split',resume_from=resumed.checkpoint)
            with self.assertRaisesRegex(ValueError,'newer checkpoint'):
                m.run_period(start='2026-11-06',end='2026-11-06',scenario=inputs,
                    checkpoint_directory=root/'split',resume_from=root/'split'/'checkpoint-2026-11-05.json')
            changed=market();changed.settlement_parameters['reserve_cost_per_day_eur']=1
            with self.assertRaisesRegex(ValueError,'configuration'):
                load_checkpoint(resumed.checkpoint,changed)
            with self.assertRaises(FileExistsError):
                m.run_period(start='2026-11-05',end='2026-11-06',scenario=inputs,
                    checkpoint_directory=root/'split')
            saved=root/'split'/'2026-11-06'/'result.json'
            original=saved.read_bytes();saved.write_bytes(original+b' ')
            with self.assertRaisesRegex(ValueError,'result checksum'):
                load_checkpoint(resumed.checkpoint,market())
            saved.write_bytes(original)
            envelope=json.loads(resumed.checkpoint.read_text());envelope['state']['last_day']='2026-11-07'
            resumed.checkpoint.write_text(json.dumps(envelope))
            with self.assertRaisesRegex(ValueError,'checksum'):
                load_checkpoint(resumed.checkpoint,market())

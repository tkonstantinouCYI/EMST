import json
from pathlib import Path
import unittest

from examples.historical_cyprus_day import build_market
from tools.prepare_cyprus_historical_day import check_grid


class HistoricalExampleTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1]/'examples/data/cyprus_2026_10_14.json'
        self.data = json.loads(path.read_text(encoding='utf-8'))

    def test_public_series_controls_and_cleaning_provenance(self):
        data = self.data
        self.assertEqual(data['day'], '2026-10-14')
        for series in list(data['forecast_mw'].values()) + list(data['actual_mw'].values()):
            self.assertEqual(len(series), 48)
            self.assertGreaterEqual(min(series), 0)
        self.assertAlmostEqual(sum(data['actual_mw']['demand'])*.5, 15070.746255)
        for product in ('FCR', 'aFRR', 'mFRR'):
            self.assertEqual(set(data['reserve_requirements'][product]), {'up', 'down'})
            for values in data['reserve_requirements'][product].values():
                self.assertEqual(len(values), 48)
        self.assertEqual(len(data['history_price_fills']), 10)
        for history in data['historical_rtbm_prices'].values():
            self.assertEqual(len(history), 3)
            for day in history:
                self.assertEqual(len(day), 6)
                self.assertLess(max(day), 999999)
        for source in data['sources']:
            self.assertEqual(len(source['sha256']), 64)
        with self.assertRaises(ValueError):
            check_grid([46309.], '2026-10-15', 30)

    def test_documented_representative_fleet_and_input_energy(self):
        market = build_market(self.data)
        thermal = [a for a in market.assets.values() if a.technology == 'thermal']
        self.assertEqual(len(thermal), 8)
        self.assertEqual(len({a.participant for a in thermal}), 8)
        self.assertEqual(sum(a.parameters['capacity_mw'] for a in thermal), 931)
        self.assertTrue(all(a.parameters['minimum_up_minutes'] == 1440 for a in thermal))
        self.assertAlmostEqual(sum(a.initial_state.power_mw for a in thermal), self.data['previous_day_oil_power_proxy_mw'])
        self.assertEqual(sum(market.forwards[0].energy_mwh), 720)
        suppliers = [a for a in market.assets.values() if a.technology == 'supplier']
        for i in range(48):
            self.assertAlmostEqual(sum(a.actual[i] for a in suppliers), self.data['actual_mw']['demand'][i])
        self.assertTrue(all(a.parameters['actual_generation_kind'] == 'metered' for a in market.assets.values() if a.technology == 'res'))

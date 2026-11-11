"""ISP ramps connect MTU average powers and the supplied initial boundary."""
from datetime import date, datetime, timezone
from unittest.mock import patch
import unittest

from emst import Market, ThermalState
from emst.calendar import day_intervals
from emst.dispatch import dispatch
from emst.isp import schedule
from emst.model import PRODUCTS, Unit
from emst.optimization import solve


class ISPAveragePowerTests(unittest.TestCase):
    def run_case(self, demand):
        market = Market()
        market.add_participant('owner')
        asset = market.add_thermal('unit', 'owner', capacity_mw=100, minimum_mw=0,
            marginal_cost=50, ramp_mw_hour=20, initial_state=ThermalState('dispatch', 20, 480))
        intervals = day_intervals(date(2026, 11, 5), 30)[:len(demand)]
        requirements = {p: [0] * len(demand) for p in PRODUCTS}
        state = {'unit': dict(stage='dispatch', on=1, power_mw=20, duration_minutes=480)}
        captured = []
        def checked_solve(model, *args, **kwargs):
            captured.extend(v.name for v in model.variables())
            return solve(model, *args, **kwargs)
        with patch('emst.dispatch.solve', side_effect=checked_solve):
            result = dispatch([asset], state, intervals, demand, [0] * len(demand),
                {'unit': [10] * len(demand)}, requirements, market.ruleset.to_dict())
        self.assertFalse(any(name.startswith('thermal_path_') or name.startswith('end_') for name in captured))
        unit = Unit('unit', 'owner', 100, 0, 50, 20, 20, 1, 16)
        fixture = schedule([unit], intervals, demand, [0] * len(demand),
            {'unit': [10] * len(demand)}, requirements,
            {'unit': dict(on=1, end_mw=20, duration_periods=16)},
            datetime(2026, 11, 4, tzinfo=timezone.utc), 'average_power_test')
        return result, fixture

    def test_boundary_and_consecutive_average_ramps(self):
        public, fixture = self.run_case([30, 20, 30])
        for rows in ([r['assets']['unit'] for r in public['rows']], fixture['rows']):
            self.assertEqual([round(r['power_mw'], 6) for r in rows], [30, 20, 30])
            self.assertEqual([round(r['ramp_reference_power_mw'], 6) for r in rows], [20, 30, 20])
            for row in rows:
                self.assertNotIn('end_mw', row)
                self.assertNotIn('nodes_mw', row)
                self.assertNotIn('phase_terminal_mw', row)
                self.assertAlmostEqual(row['power_mw'] * .5, 10 + row['up_mwh'] - row['down_mwh'])
        self.assertTrue(public['physical_security_feasible'])

    def test_initial_boundary_limits_the_first_average(self):
        public, fixture = self.run_case([40, 40])
        self.assertAlmostEqual(public['rows'][0]['assets']['unit']['power_mw'], 30)
        self.assertAlmostEqual(public['rows'][1]['assets']['unit']['power_mw'], 40)
        self.assertAlmostEqual(public['slack_by_period'][0]['energy_shortage_mwh'], 5)
        self.assertAlmostEqual(fixture['rows'][0]['power_mw'], 30)

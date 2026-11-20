"""Forced-path checks for the experimental hot-only virtual-phase formulation."""
from copy import deepcopy
import unittest
import pulp as lp
from examples.synthetic_three_configuration_isp import plant, build_inputs
from emst.configurations import boundary, states, key_at_boundary
from emst.configurations_reference import occupancy as reference
from emst.configurations_compact import occupancy as compact
from emst.configurations_virtual import occupancy as virtual
from tools.hourly_isp_suggestions import hourly_copy, capture


def small_data():
    data = deepcopy(plant().parameters['plant_configurations'])
    for c in data['components'].values():
        c.update(minimum_up_minutes=0, minimum_down_minutes=0)
    for e in data['transitions']:
        if len(e['profile_mw']) > 2:
            e['profile_mw'] = [e['profile_mw'][0], e['profile_mw'][-1]]
            e['components_by_step'] = [e['components_by_step'][0], e['components_by_step'][-1]]
    initial = dict(configuration='GT', power_mw=40,
        component_histories={c: dict(on=c=='lead_GT', duration_minutes=1440) for c in data['components']})
    return data, initial


def forced(function, data, initial, path):
    specs = states(data)
    m = lp.LpProblem('forced_virtual', lp.LpMinimize)
    z, x, counts = function(m, data, specs, list(specs), len(path), 'unit', initial, key_at_boundary(initial))
    for t, state in enumerate(path):
        m += z[state, t] == 1
    m += lp.lpSum(x[e['id'], t]*e['cost_eur'] for e in data['transitions'] for t in range(len(path)))
    status = m.solve(lp.HiGHS(msg=False, threads=1, gapRel=0, gapAbs=0))
    return status, lp.value(m.objective), m, counts


class VirtualConfigurationsTests(unittest.TestCase):
    def test_count_includes_off_edges(self):
        data, initial = small_data()
        _, _, m, counts = forced(virtual, data, initial, ['GT']*24)
        self.assertEqual(sum(v.isBinary() for v in m.variables()), 23*24)
        self.assertEqual(counts['phase_binary_count'], 12*24)
        self.assertEqual(counts['balancing_selection_binary_count'], 3*24)
        self.assertEqual(counts['transition_start_binary_count'], 8*24)

    def test_forced_paths_match_reference(self):
        data, initial = small_data()
        paths = [(['GT']*4, True),
            (['GT', 'GT_to_GT_ST:0', 'GT_to_GT_ST:1', 'GT_ST'], True),
            (['GT_to_GT_ST:0', 'GT_to_GT_ST:1', 'GT_ST_to_TWO_GT_ST:0'], True),
            (['GT', 'TWO_GT_ST'], False),
            (['GT_to_GT_ST:0', 'GT'], False),
            (['GT_to_OFF:0', 'OFF', 'OFF_to_GT:0', 'OFF_to_GT:1'], True)]
        for path, expected in paths:
            objectives = []
            for function in (reference, compact, virtual):
                status, value, m, _ = forced(function, data, initial, path)
                self.assertEqual(status == lp.LpStatusOptimal, expected, (function.__module__, path))
                if expected:
                    objectives.append(value)
                    for v in m.variables():
                        if '_state_' in v.name or '_component_' in v.name:
                            self.assertAlmostEqual(v.value(), round(v.value()), places=6)
            if expected:
                self.assertAlmostEqual(max(objectives), min(objectives))

    def test_inherited_transition_and_horizon_edge(self):
        data, initial = small_data()
        initial.update(configuration='GT', transition=dict(id='GT_to_GT_ST', elapsed_minutes=30))
        initial['component_histories']['ST'] = dict(on=True, duration_minutes=30)
        for path, expected in [(['GT_to_GT_ST:1'], True),
            (['GT_to_GT_ST:1', 'GT_ST_to_TWO_GT_ST:0'], True), (['GT_ST'], False)]:
            for function in (reference, compact, virtual):
                self.assertEqual(forced(function, data, initial, path)[0] == lp.LpStatusOptimal, expected)

    def test_minimum_history_is_preserved(self):
        data, initial = small_data()
        data['components']['lead_GT']['minimum_up_minutes'] = 120
        initial['component_histories']['lead_GT']['duration_minutes'] = 0
        for function in (reference, compact, virtual):
            self.assertEqual(forced(function, data, initial, ['GT_to_OFF:0'])[0], lp.LpStatusInfeasible)

    def test_full_dispatch_objective_matches_compact(self):
        results = []
        for variant in ('compact', 'virtual'):
            k, _, responses = hourly_copy(build_inputs(True), configuration_formulation=variant)
            # A short physical ISP case exercises the same shared power/reserve kernel.
            for field in ('intervals', 'demand', 'res'):
                k[field] = k[field][:6]
            for field in ('commercial', 'requirements', 'mandatory_on', 'res_forecasts'):
                k[field] = {a: v[:6] for a, v in k[field].items()}
            m = capture(k, responses)['model']
            m += m._emst_configuration_states['CC']['TWO_GT_ST_to_GT_ST:0', 1] == 1
            status = m.solve(lp.HiGHS(msg=False, threads=1, gapRel=0, gapAbs=0))
            self.assertEqual(status, lp.LpStatusOptimal)
            results.append(lp.value(m.objective))
        self.assertAlmostEqual(*results, places=4)


if __name__ == '__main__':
    unittest.main()

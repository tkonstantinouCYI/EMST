"""Matched no-CC/one-CC hourly initial ISP benchmark, without downstream stages."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
import highspy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.synthetic_three_configuration_isp import build_inputs
from examples.synthetic_handoff_isp import solve_model
from tools.hourly_isp_suggestions import hourly_copy, capture, decisions
from emst.optimization import primal_violation
from emst.configurations import configured


def run(out, include_cc, formulation='virtual', forced=False, transition_reserves=False):
    out.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    k, audit, responses = hourly_copy(build_inputs(include_cc,transition_reserves=transition_reserves), configuration_formulation=formulation)
    prepare = time.perf_counter() - started
    (out / 'hourly_inputs.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    box = capture(k, responses)
    model = box['model']
    if forced:
        z = model._emst_configuration_states['CC']
        model += z['TWO_GT_ST_to_GT_ST:0', 1] == 1
        model += z['GT_ST_to_TWO_GT_ST:0', 8] == 1
    binaries = [v for v in model.variables() if v.isBinary()]
    report = dict(include_cc=include_cc, formulation=formulation if include_cc else None, forced_transitions=forced, transition_reserves=transition_reserves,
        interval_minutes=60, intervals=24, preparation_seconds=prepare,
        construction_seconds=box['construction_seconds'], variables=len(model.variables()),
        binaries=len(binaries), constraints=len(model.constraints),
        cc_binaries=sum(v.name.startswith('plant_CC_') for v in binaries),
        note='Synthetic assumptions. Initial ISP MILP only, no pricing/noon rerun/RTBM/settlement. Existing demand and ordinary-unit schedules unchanged; CC commercial schedule zero. Shortage slacks remain enabled.')
    if include_cc and formulation == 'virtual':
        assert report['cc_binaries'] == 23 * 24
    started = time.perf_counter()
    model.writeMPS(str(out / 'initial_ISP.mps'))
    report['export_seconds'] = time.perf_counter() - started
    print(json.dumps(dict(event='built', path=str(out), **report)), flush=True)
    result = solve_model(out, 120, 4, 'choose')
    if result['incumbent_available']:
        h = highspy.Highs()
        h.setOptionValue('output_flag', False)
        assert h.readModel(str(out / 'initial_ISP.mps')) == highspy.HighsStatus.kOk
        assert h.readSolution(str(out / 'solution.txt'), 0) == highspy.HighsStatus.kOk
        values = dict(zip(h.getLp().col_names_, h.getSolution().col_value))
        for v in model.variables():
            v.varValue = values[v.name]
        result['constraint_violation'] = primal_violation(model.variables(), list(model.constraints.values()))
        assert result['constraint_violation'] <= 1e-5
        result['integer_violation'] = max(abs(v.value()-round(v.value())) for v in binaries)
        assert result['integer_violation'] <= 1e-5
        rows = decisions(model, k['assets'])
        thermal = [a for a in k['assets'] if a.technology == 'thermal']
        transition_cost = 0
        for j, row in enumerate(rows):
            row.update(start=audit['interval_starts'][j], duration_minutes=60)
            for i, a in enumerate(thermal):
                up_name, down_name = (f'plant_up_{i}_{j}', f'plant_down_{i}_{j}') if configured(a) else (f'up_{i}_{j}', f'down_{i}_{j}')
                up, down = 2*values[up_name], 2*values[down_name]
                row['assets'][a.id].update(energy_up_mwh=up, energy_down_mwh=down,
                    power_mw=audit['commercial_mwh'][a.id][j]+up-down)
                if configured(a):
                    row['assets'][a.id]['reserve_capacity_mw']={product:values[f'plant_CC_reserve_{product}_{j}']
                        for product in k['requirements']}
                    transition_cost += sum(e['cost_eur'] * values.get(f'plant_CC_transition_start_{ei}_{j}', 0)
                        for ei, e in enumerate(a.parameters['plant_configurations']['transitions']))
        (out / 'hourly_dispatch.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        result['energy_shortage_mwh'] = 2*sum(v for name,v in values.items() if name.startswith('energy_shortage_mwh_'))
        result['transition_cost_eur'] = transition_cost
        result['cc_transition_intervals'] = sum(':' in r['assets'].get('CC', {}).get('configuration_state', '') for r in rows)
        result['cc_transition_reserve_mw_hours']={product:sum(r['assets']['CC']['reserve_capacity_mw'][product]
            for r in rows if ':' in r['assets'].get('CC',{}).get('configuration_state','')) for product in k['requirements']} if include_cc else {}
        result['reserve_shortage_mw_hours']={product:sum(values.get(f'reserve_shortage_mw_{product}_{j}',0) for j in range(24))
            for product in k['requirements']}
    report['solve'] = result
    report['total_seconds'] = prepare + box['construction_seconds'] + report['export_seconds'] + result['read_seconds'] + result['solve_wall_seconds']
    (out / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(event='finished', path=str(out), **report)), flush=True)
    return report


def main():
    out = ROOT / 'output/handoff-isp' / ('hourly-one-virtual-cc-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    reports = []
    # Serial solves, same solver/version/options, cold starts and zero gap target.
    for repeat in range(3):
        for include in ((False, True) if repeat % 2 == 0 else (True, False)):
            reports.append(run(out / f'repeat-{repeat}-cc-{int(include)}', include))
    forced = run(out / 'forced-transitions', True, forced=True)
    reference = run(out / 'forced-compact-reference', True, formulation='compact', forced=True)
    assert forced['solve']['proven_optimal'] and reference['solve']['proven_optimal']
    assert abs(forced['solve']['objective'] - reference['solve']['objective']) < 1e-4
    assert abs(forced['solve']['energy_shortage_mwh'] - reference['solve']['energy_shortage_mwh']) < 1e-5
    (out / 'comparison.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print('COMPARISON=' + str(out / 'comparison.json'), flush=True)


if __name__ == '__main__':
    main()

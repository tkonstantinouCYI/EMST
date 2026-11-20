"""Standalone 24-hour initial ISP experiment; no half-hour reconstruction."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples import synthetic_handoff_isp as source
from tools.hourly_isp_suggestions import hourly_copy, capture, decisions, nearest_hours
from emst.optimization import primal_violation


def main():
    out = ROOT / 'output/handoff-isp' / ('hourly-without-cc-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    original, _ = source.build_inputs()
    original['assets'] = [a for a in original['assets'] if 'plant_configurations' not in a.parameters]
    ids = {a.id for a in original['assets']}
    for key in ('states', 'commercial', 'mandatory_on'):
        original[key] = {a: v for a, v in original[key].items() if a in ids}
    k, audit, responses = hourly_copy(original)
    preparation = time.perf_counter() - started
    assert [nearest_hours(x) for x in (0, 30, 90, 390, 630)] == [0, 1, 2, 7, 11]
    assert len(k['intervals']) == 24
    for a in k['assets']:
        assert abs(sum(audit['commercial_mwh'][a.id]) - sum(original['commercial'][a.id])) < 1e-7
        if a.technology == 'thermal':
            old = next(v for v in original['assets'] if v.id == a.id)
            assert a.parameters['ramp_mw_hour'] == 2 * old.parameters['ramp_mw_hour']
    (out / 'hourly_inputs_real_units.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    captured = capture(k, responses)
    model = captured['model']
    stats = dict(variables=len(model.variables()), binaries=sum(v.isBinary() for v in model.variables()),
        constraints=len(model.constraints), preparation_seconds=preparation,
        construction_seconds=captured['construction_seconds'])
    started = time.perf_counter()
    model.writeMPS(str(out / 'initial_ISP.mps'))
    stats['export_seconds'] = time.perf_counter() - started
    print(json.dumps(dict(output=str(out), model=stats)), flush=True)
    result = source.solve_model(out, 120, 4, 'choose')
    if result['incumbent_available']:
        import highspy
        h = highspy.Highs()
        h.setOptionValue('output_flag', False)
        h.readModel(str(out / 'initial_ISP.mps'))
        h.readSolution(str(out / 'solution.txt'), 0)
        values = dict(zip(h.getLp().col_names_, h.getSolution().col_value))
        for v in model.variables():
            v.varValue = values[v.name]
        result['original_model_primal_violation'] = primal_violation(model.variables(), list(model.constraints.values()))
        assert result['original_model_primal_violation'] <= 1e-5
        rows = decisions(model, k['assets'])
        thermal = [a for a in k['assets'] if a.technology == 'thermal']
        for j, row in enumerate(rows):
            row['start'] = audit['interval_starts'][j]
            row['duration_minutes'] = 60
            for i, a in enumerate(thermal):
                up = 2 * values[f'up_{i}_{j}']
                down = 2 * values[f'down_{i}_{j}']
                row['assets'][a.id].update(energy_up_mwh=up, energy_down_mwh=down,
                    dispatch_power_mw=audit['commercial_mwh'][a.id][j] + up - down)
        (out / 'hourly_dispatch.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        result['energy_shortage_mwh'] = 2 * sum(v for n, v in values.items() if n.startswith('energy_shortage_mwh_'))
        result['energy_surplus_mwh'] = 2 * sum(v for n, v in values.items() if n.startswith('energy_surplus_mwh_'))
        result['reserve_shortage_mw_hours'] = sum(v for n, v in values.items() if n.startswith('reserve_shortage_mw_'))
    result['total_preparation_build_export_load_solve_seconds'] = preparation + stats['construction_seconds'] + stats['export_seconds'] + result['read_seconds'] + result['solve_wall_seconds']
    report = dict(model=stats, solve=result, interval_minutes=60, intervals=24,
        scope='Initial ISP MILP only; no fixed-discrete pricing, noon rerun, RTBM, settlement or half-hour reconstruction.',
        encoding=audit['kernel_encoding'], rounding_policy=audit['rounding_policy'])
    (out / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

"""Time the initial handoff ISP after removing configured plants, without rescaling."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples import synthetic_handoff_isp as source


def main():
    out = ROOT / 'output' / 'handoff-isp' / ('without-cc-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    original = source.build_inputs
    captured = {}

    def inputs():
        k, snapshot = original()
        removed = {a.id for a in k['assets'] if 'plant_configurations' in a.parameters}
        k['assets'] = [a for a in k['assets'] if a.id not in removed]
        for key in ('states', 'commercial', 'mandatory_on'):
            k[key] = {a: v for a, v in k[key].items() if a not in removed}
        snapshot['assets'] = [a for a in snapshot['assets'] if a['id'] not in removed]
        snapshot['states'] = k['states']
        snapshot['commercial_mwh'] = k['commercial']
        snapshot['mandatory_on'] = k['mandatory_on']
        capacity = sum(a.parameters['capacity_mw'] for a in k['assets'] if a.technology == 'thermal')
        metadata = dict(removed=sorted(removed), thermal_capacity_mw=capacity,
            peak_net_demand_mw=max(d-r for d, r in zip(k['demand'], k['res'])),
            note='Demand, RES, remaining commercial schedules, reserve requirements and initial states unchanged. Security slacks enabled as in original. Initial ISP only; no pricing or downstream stages.')
        snapshot['experiment'] = metadata
        captured.update(metadata=metadata, inputs=k)
        return k, snapshot

    # Retain the identical export and native HiGHS solve path used by the baseline.
    with patch.object(source, 'build_inputs', side_effect=inputs):
        started = time.perf_counter()
        stats = source.export_model(out)
    export_wall = time.perf_counter() - started
    print(json.dumps(dict(output=str(out), model=stats, experiment=captured['metadata'])), flush=True)
    result = source.solve_model(out, 120, 4, 'choose')
    if result['incumbent_available']:
        import highspy
        h = highspy.Highs()
        h.setOptionValue('output_flag', False)
        h.readModel(str(out / 'initial_ISP.mps'))
        h.readSolution(str(out / 'solution.txt'), 0)
        values = dict(zip(h.getLp().col_names_, h.getSolution().col_value))
        # Evaluate security slacks directly from the exported solution.
        result['security_slack_totals'] = {prefix: sum(v for name, v in values.items() if name.startswith(prefix))
            for prefix in ('energy_shortage_mwh_', 'energy_surplus_mwh_', 'reserve_shortage_mw_')}
        (out / 'solution.json').write_text(json.dumps(values), encoding='utf-8')
    result['total_export_load_solve_seconds'] = export_wall + result['read_seconds'] + result['solve_wall_seconds']
    report = dict(model=stats, experiment=captured['metadata'], solve=result)
    (out / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()

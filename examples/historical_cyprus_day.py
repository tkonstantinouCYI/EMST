"""Run 14 October 2026 using public TSOC series and explicit modelling assumptions."""
import argparse
from dataclasses import asdict
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import time

from emst import Forward, Market, ThermalState

DATA = Path(__file__).parent / 'data' / 'cyprus_2026_10_14.json'


def build_market(data):
    market = Market()
    # Nameplate capacities from EAC/NECP. All other technical/economic values
    # below are scenario assumptions, not confidential unit declarations.
    fleet = []
    for prefix, count, capacity, minimum, cost, ramp, startup, up_minutes in (
        ('VASS_CCGT', 2, 220, 66, 210, 1500, 4000, 120),
        ('VASS_ST', 3, 130, 52, 240, 300, 2000, 120),
        ('DHEK_ICE', 2, 51, 15.3, 260, 600, 500, 60),
        ('DHEK_ST', 6, 60, 24, 300, 180, 1000, 120),
        ('MONI_GT', 4, 37.5, 11.25, 380, 1200, 300, 30),
        ('VASS_GT', 1, 38, 11.4, 370, 1200, 300, 30),
    ):
        for number in range(1, count+1):
            fleet.append(dict(id=f'{prefix}_{number}', capacity=capacity, minimum=minimum,
                              cost=cost, ramp=ramp, startup=startup, up_minutes=up_minutes))
    # Allocate previous day's last half-hour Oil MW by available capacity.
    # This is a boundary proxy, not an observed unit state.
    initial_total = data['previous_day_oil_power_proxy_mw']
    # Representative available fleet, not all historical nameplate capacity.
    selected = {'VASS_CCGT_1', 'VASS_CCGT_2', 'VASS_ST_2', 'VASS_ST_3',
                'DHEK_ST_2', 'DHEK_ST_3', 'DHEK_ST_4', 'DHEK_ICE_1'}
    fleet = [u for u in fleet if u['id'] in selected]
    online_capacity = sum(u['capacity'] for u in fleet)
    for i, unit in enumerate(fleet):
        owner = unit['id'] + '_owner'
        market.add_participant(owner, strategies={'thermal':'marginal_cost'})
        initial = initial_total * unit['capacity'] / online_capacity
        market.add_thermal(unit['id'], owner, capacity_mw=unit['capacity'],
            minimum_mw=.3*unit['capacity'], marginal_cost=unit['cost']+i,
            ramp_mw_hour=unit['ramp'], startup_cost=unit['startup'],
            minimum_up_minutes=1440, minimum_down_minutes=60,
            reserve_cost=5, energy_up_price=unit['cost']+i+15,
            energy_down_price=unit['cost']+i-15,
            initial_state=ThermalState('dispatch', initial, 0))
    for owner in ('supplier_T', 'supplier_D', 'solar_owner', 'wind_owner'):
        market.add_participant(owner)
    for asset, owner, fraction, voltage in (
        ('load_T', 'supplier_T', .6, 'HV'), ('load_D', 'supplier_D', .4, 'LV')
    ):
        market.add_supplier(asset, owner, voltage=voltage,
            demand_forecast=[fraction*v for v in data['forecast_mw']['demand']],
            actual_consumption=[fraction*v for v in data['actual_mw']['demand']])
    for technology in ('solar', 'wind'):
        # Preserve published delivered generation, including any historical
        # curtailment, instead of inventing available/unconstrained production.
        market.add_res(technology, technology+'_owner',
            generation_forecast=data['forecast_mw'][technology],
            actual_generation=data['actual_mw'][technology],
            actual_generation_kind='metered', grant_res=False)
    market.add_forward(Forward('assumed_bilateral', 'VASS_CCGT_1_owner', 'supplier_T',
        'VASS_CCGT_1', tuple([15.]*48), 230, buyer_entity='load_T'))
    return market


def verify(result, data, market):
    checks = dict(result.audit['checks'])
    checks['48_MTUs_288_RTUs_two_ISPs'] = len(result.dam['prices_eur_mwh']) == 48 and len(result.rtbm) == 288 and len(result.isp_runs) == 2
    checks['historical_actual_energy_preserved'] = (
        abs(sum(r['actual_demand_mwh'] for r in result.rtbm)-sum(data['actual_mw']['demand'])*.5) < 1e-6 and
        abs(sum(r['actual_RES_mwh'] for r in result.rtbm)-sum(data['actual_mw']['solar']+data['actual_mw']['wind'])*.5) < 1e-6)
    checks['full_demand_cleared'] = all(abs(result.dam['accepted_mwh'][o['id']]-o['energy_mwh']) < 1e-5 for o in result.orders if o['side']=='buy')
    checks['no_ISP_or_RTBM_slack'] = all(r['physical_security_feasible'] for r in result.isp_runs) and all(
        abs(r['slack']['energy_shortage_mwh']) < 1e-6 and abs(r['slack']['energy_surplus_mwh']) < 1e-6
        for r in result.rtbm)
    report = result.to_readable(audience='operator')
    stages = report['simulation_summary']['days'][0]['market_stages']
    with localcontext() as context:
        context.prec = 80
        for owner in market.participants:
            fp = stages['settlement']['participants'][owner]['financial_position']
            expected = sum((Decimal(e['cash_eur'])*(int(e['account'].startswith(f'participant:{owner}:'))-int(e['counterparty'].startswith(f'participant:{owner}:'))) for e in result.ledger), Decimal(0))
            if Decimal(fp['daily_total_cash_eur']) != expected:
                raise AssertionError('Participant financial position does not reconcile')
            for row in fp['intervals']:
                period = f"{data['day']}:30:{row['period_index']:03}"
                entries = [e for e in result.ledger if e['period'] == period]
                mtu_cash = sum((Decimal(e['cash_eur'])*(int(e['account'].startswith(f'participant:{owner}:'))-int(e['counterparty'].startswith(f'participant:{owner}:'))) for e in entries), Decimal(0))
                if Decimal(row['total_cash_eur']) != mtu_cash:
                    raise AssertionError('MTU financial position does not reconcile')
    checks['all_participant_financial_positions_reconcile'] = True
    checks['only_UA1_UA2_uplift_postings'] = not any(e['component'] in ('S18','S19','S20','S21','S22') for e in result.ledger)
    for code in ('S16', 'S17'):
        pool = sum((Decimal(e['cash_eur']) for e in result.ledger if e['component']==code and not e['account'].startswith('participant:')), Decimal(0))
        paid = sum((Decimal(e['cash_eur']) for e in result.ledger if e['component']==code and e['account'].startswith('participant:')), Decimal(0))
        checks[code+'_cost_recovery'] = abs(pool+paid) < Decimal('.000001')
    checks['shared_report_excludes_financial_positions'] = 'financial_position' not in json.dumps(result.to_readable())
    return dict(passed=all(checks.values()), checks=checks,
        stages=stages['settlement']['summary'],
        actual_demand_mwh=sum(r['actual_demand_mwh'] for r in result.rtbm),
        actual_res_mwh=sum(r['actual_RES_mwh'] for r in result.rtbm),
        physical_imbalance_mwh=result.manifest['physical_imbalance_mwh'],
        max_absolute_RTU_physical_imbalance_mwh=max(abs(r['physical_imbalance_mwh']) for r in result.rtbm),
        dam_mean_absolute_price_difference_from_observed_eur_mwh=sum(abs(a-b) for a,b in zip(result.dam['prices_eur_mwh'], data['benchmark']['dam_prices_eur_mwh']))/48,
        participant_count=len(market.participants), participant_MTU_financial_positions=len(market.participants)*48,
        historical_price_fill_count=len(data['history_price_fills']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--output', type=Path, default=Path('output/historical-cyprus/2026-10-14'))
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding='utf-8'))
    market = build_market(data)
    started = time.perf_counter()
    result = market.run(time_index=market.time_index(data['day']),
        system_forecasts={k:data['forecast_mw'][k] for k in ('demand','res')},
        system_observations=dict(demand=data['actual_mw']['demand'],
            res=[a+b for a,b in zip(data['actual_mw']['solar'], data['actual_mw']['wind'])]),
        reserve_requirements=data['reserve_requirements'],
        historical_rtbm_prices={int(k):v for k,v in data['historical_rtbm_prices'].items()})
    elapsed = time.perf_counter()-started
    result.save(args.output / 'raw')
    raw = args.output / 'raw_results.json'
    raw.write_text(json.dumps(asdict(result), indent=2, allow_nan=False), encoding='utf-8')
    checksum = hashlib.sha256(raw.read_bytes()).hexdigest()
    files = result.save_readable(args.output)
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == checksum
    checks = verify(result, data, market)
    checks.update(simulation_elapsed_seconds=elapsed, raw_results_sha256=checksum)
    (args.output/'verification.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    (args.output/'scenario_inputs.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    assumptions = dict(scenario='historical_public_data_with_assumed_committed_fleet',
        thermal_units=8, available_capacity_mw=931, commitment='all_selected_units_online_for_day',
        minimum_output_fraction=.3, initial_power='previous_day_Oil_average_allocated_by_capacity',
        observations='half_hour_actuals_repeated_six_times', forecasts='single_published_snapshot_no_revisions',
        supplier_shares={'supplier_T':.6, 'supplier_D':.4},
        forward_contract=dict(energy_mwh_per_mtu=15, price_eur_mwh=230),
        storage=False, interconnections=False, reserve_products=['FCR','aFRR','mFRR'],
        tariffs='default_synthetic_library_bundle', historical_price_fills=len(data['history_price_fills']),
        official_historical_replay=False, source_dataset_sha256=hashlib.sha256(args.data.read_bytes()).hexdigest())
    (args.output/'assumptions.json').write_text(json.dumps(assumptions, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in checks.items() if k not in ('checks','stages')}, indent=2))
    print(f"Readable results: {files['operator']}")
    if not checks['passed']: raise SystemExit('Historical scenario verification failed; see verification.json')


if __name__ == '__main__': main()

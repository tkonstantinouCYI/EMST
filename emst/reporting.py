"""Stage-oriented reports with explicit shared and participant-private views."""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .settlement import CATALOGUE


STAGES = ('forward_market', 'day_ahead_market', 'isp', 'rtbm', 'settlement')


def _interval(at, hours, period):
    start = datetime.fromisoformat(at).astimezone(ZoneInfo('Europe/Nicosia'))
    return dict(period_index=period, start=start.isoformat(),
                end=(start + timedelta(hours=hours)).isoformat())


def _cash(entry, participant):
    prefix = f'participant:{participant}:'
    sign = int(entry['account'].startswith(prefix)) - int(entry['counterparty'].startswith(prefix))
    return Decimal(entry['cash_eur']) * sign


def _cash_totals(entries, participant):
    totals = {}
    for entry in entries:
        value = _cash(entry, participant)
        if value or entry['account'].startswith(f'participant:{participant}:'):
            code = entry['component']
            totals[code] = totals.get(code, Decimal(0)) + value
    return {code: dict(description=CATALOGUE[code], cash_eur=str(value))
            for code, value in sorted(totals.items())}


def _cash_lines(entries, participant):
    lines = []
    for entry in entries:
        prefix = f'participant:{participant}:'
        if not (entry['account'].startswith(prefix) or entry['counterparty'].startswith(prefix)):
            continue
        lines.append(dict(posting_id=entry['id'], component=entry['component'],
                          description=CATALOGUE[entry['component']], period=entry['period'],
                          quantity=entry['quantity'], reference_price=entry['price'],
                          cash_eur=str(_cash(entry, participant))))
    return lines


def _financial_position(result, participant):
    fields = ('forward_market_cash_eur', 'day_ahead_market_cash_eur', 'isp_cash_eur',
              'rtbm_cash_eur', 'settlement_cash_eur', 'ua1_cash_eur', 'ua2_cash_eur')
    allocation = {'S01': fields[0], 'S02': fields[1], 'S16': fields[5], 'S17': fields[6]}
    allocation.update({code: fields[2] for code in ('S05', 'S06', 'S07', 'S08', 'S13')})
    allocation.update({code: fields[3] for code in ('S03', 'S10', 'S12')})
    times = result.inputs['time_index']
    periods = {f"{result.manifest['day']}:30:{t:03}": t for t in range(len(times))}
    buckets = [{field: Decimal(0) for field in fields} for _ in times]
    non_mtu = []
    with localcontext() as context:
        context.prec = 80
        for entry in result.ledger:
            cash = _cash(entry, participant)
            if not cash:
                continue
            if entry['period'] not in periods:
                non_mtu.append(entry)
                continue
            field = allocation.get(entry['component'], 'settlement_cash_eur')
            buckets[periods[entry['period']]][field] += cash
        totals = {field: sum((bucket[field] for bucket in buckets), Decimal(0)) for field in fields}
        intervals = [dict(_interval(at, .5, t), **{k: str(v) for k, v in bucket.items()},
                          total_cash_eur=str(sum(bucket.values(), Decimal(0))))
                     for t, (at, bucket) in enumerate(zip(times, buckets))]
        mtu_total = sum(totals.values(), Decimal(0))
        non_mtu_cash = sum((_cash(entry, participant) for entry in non_mtu), Decimal(0))
        return dict(intervals=intervals,
            totals=dict(**{k: str(v) for k, v in totals.items()}, total_cash_eur=str(mtu_total)),
            non_mtu_cash_eur=str(non_mtu_cash), non_mtu_postings=_cash_lines(non_mtu, participant),
            daily_total_cash_eur=str(mtu_total + non_mtu_cash))


def _asset_interval(row, asset, timestamp):
    fields = dict(timestamp, asset_id=asset, instructed_energy_mwh=row['energy_mwh'],
                  energy_up_mwh=row['up_mwh'], energy_down_mwh=row['down_mwh'],
                  average_power_mw=row['power_mw'])
    for source, target in (('end_mw', 'ending_power_mw'), ('soc_mwh', 'ending_soc_mwh'),
                           ('phase', 'thermal_stage'), ('actual_mwh', 'actual_energy_mwh')):
        if source in row:
            fields[target] = row[source]
    return fields


def _shared(result):
    times = result.inputs['time_index']
    stages = {name: dict(summary={}) for name in STAGES}
    forward = [e for e in result.ledger if e['component'] == 'S01']
    stages['forward_market']['summary'] = dict(
        contracted_energy_mwh=float(sum((Decimal(e['quantity']) for e in forward), Decimal(0))),
        intervals=[dict(_interval(at, .5, t), contracted_energy_mwh=float(sum(
            (Decimal(e['quantity']) for e in forward if e['period'].endswith(f':30:{t:03}')),
            Decimal(0)))) for t, at in enumerate(times)])
    dam_intervals = []
    for t, at in enumerate(times):
        sold = sum(result.dam['accepted_mwh'][o['id']] for o in result.orders
                   if o['period'] == t and o['side'] == 'sell')
        dam_intervals.append(dict(_interval(at, .5, t), traded_energy_mwh=sold,
                                  clearing_price_eur_mwh=result.dam['prices_eur_mwh'][t]))
    stages['day_ahead_market']['summary'] = dict(
        total_traded_energy_mwh=sum(r['traded_energy_mwh'] for r in dam_intervals),
        intervals=dam_intervals)
    active = {r['period']: r['isp_run'] for r in result.rtbm}
    runs = []
    for run in result.isp_runs:
        intervals = []
        for row in run['rows']:
            awards = {product: sum(a['reserve_mw'].get(product, 0) for a in row['assets'].values())
                      for product in row['reserve_prices']}
            intervals.append(dict(_interval(row['at'], row['hours'], row['period']),
                effective_for_dispatch=active.get(row['period']) == run['id'],
                energy_up_mwh=sum(a['up_mwh'] for a in row['assets'].values()),
                energy_down_mwh=sum(a['down_mwh'] for a in row['assets'].values()),
                energy_shadow_price_eur_mwh=row['price_eur_mwh'],
                indicative_upward_offer_price_eur_mwh=max(
                    (a['up_offer_eur_mwh'] for a in row['assets'].values() if a['up_mwh'] > 1e-7), default=None),
                indicative_downward_offer_price_eur_mwh=min(
                    (a['down_offer_eur_mwh'] for a in row['assets'].values() if a['down_mwh'] > 1e-7), default=None),
                reserve_requirements_mw={p: result.inputs['reserve_requirements_mw'][p][row['period']]
                                         for p in awards},
                procured_reserve_mw=awards, reserve_prices_eur_mw_period=deepcopy(row['reserve_prices']),
                slack=deepcopy(row['slack'])))
        runs.append(dict(run_id=run['id'], summary=dict(solved_at=run['cutoff'], intervals=intervals)))
    stages['isp'] = dict(runs=runs)
    intervals = []
    for row in result.rtbm:
        up = sum(a['up_mwh'] for a in row['assets'].values())
        down = sum(a['down_mwh'] for a in row['assets'].values())
        intervals.append(dict(_interval(row['at'], row['hours'], row['period']),
            energy_up_mwh=up, energy_down_mwh=down,
            system_position='short' if up > down + 1e-7 else 'long' if down > up + 1e-7 else 'balanced',
            marginal_price_eur_mwh=row['price_eur_mwh'], demand_mwh=row['actual_demand_mwh'],
            res_generation_mwh=row['actual_RES_mwh'], physical_imbalance_mwh=row['physical_imbalance_mwh']))
    stages['rtbm']['summary'] = dict(intervals=intervals,
        total_energy_up_mwh=sum(r['energy_up_mwh'] for r in intervals),
        total_energy_down_mwh=sum(r['energy_down_mwh'] for r in intervals))
    settlement = []
    for t, at in enumerate(times):
        batch = [r for r in result.rtbm if r['period'] == t]
        offers = [a for r in batch for a in r['assets'].values()]
        net = sum(a['up_mwh'] - a['down_mwh'] for a in offers
                  if a.get('reason', 'ordinary_market') in ('ordinary_market', 'RES_down_offer'))
        price = batch[0]['settlement_price_eur_mwh']
        record = dict(_interval(at, .5, t), imbalance_price_eur_mwh=price,
            system_position='short' if net >= 0 else 'long',
            upward_labeo_eur_mwh=max((a['up_offer_eur_mwh'] for a in offers if a['up_mwh'] > 1e-7), default=price),
            downward_labeo_eur_mwh=min((a['down_offer_eur_mwh'] for a in offers if a['down_mwh'] > 1e-7), default=price))
        for code, name, account in (('S16', 'ua1_cost_pool_eur', 'A-B'), ('S17', 'ua2_cost_pool_eur', 'A-C')):
            record[name] = str(sum((Decimal(e['cash_eur']) for e in result.ledger
                if e['component'] == code and e['account'] == account and e['period'].endswith(f':{t:03}')), Decimal(0)))
        for name, codes in (('ua2_consumption_cost_pool_eur', {'S05', 'S08', 'S09', 'S10', 'S11', 'S13'}),
                            ('ua2_imbalance_cost_pool_eur', {'S06', 'S07'})):
            record[name] = str(sum((Decimal(e['cash_eur']) for e in result.ledger
                if e['component'] in codes and e['period'].endswith(f':{t:03}')), Decimal(0)))
        settlement.append(record)
    stages['settlement']['summary'] = dict(intervals=settlement)
    return stages


def _participant(result, participant):
    stages = {name: {} for name in STAGES}
    assets = {a['id'] for a in result.inputs['assets'] if a['participant'] == participant}
    times = result.inputs['time_index']
    contracts = []
    for entry in result.ledger:
        prefix = f'participant:{participant}:'
        if entry['component'] != 'S01' or not (entry['account'].startswith(prefix) or entry['counterparty'].startswith(prefix)):
            continue
        selling = entry['account'] == f'participant:{participant}:private'
        contracts.append(dict(contract_id=entry['id'].removeprefix('forward:').rsplit(':', 1)[0],
            period=entry['period'], side='sell' if selling else 'buy',
            counterparty=(entry['counterparty'] if selling else entry['account']).split(':')[1],
            energy_mwh=float(entry['quantity']), price_eur_mwh=float(entry['price']),
            cash_eur=str(_cash(entry, participant))))
    stages['forward_market'] = dict(intervals=contracts)
    orders = []
    for order in result.orders:
        if order['participant'] != participant:
            continue
        orders.append(dict(_interval(times[order['period']], .5, order['period']),
            order_id=order['id'], asset_id=order['asset'], side=order['side'],
            offered_energy_mwh=order['energy_mwh'], offer_price_eur_mwh=order['price_eur_mwh'],
            accepted_energy_mwh=result.dam['accepted_mwh'][order['id']],
            clearing_price_eur_mwh=result.dam['prices_eur_mwh'][order['period']], block_id=order['block']))
    stages['day_ahead_market'] = dict(orders=orders)
    stages['day_ahead_market']['totals'] = {
        side + '_mwh': sum(o['accepted_energy_mwh'] for o in orders if o['side'] == side)
        for side in ('buy', 'sell')}
    stages['day_ahead_market']['quantity_convention'] = 'positive_sell_negative_buy'
    stages['day_ahead_market']['intervals'] = [dict(
        _interval(at, .5, t),
        quantity_mwh=sum(o['accepted_energy_mwh'] * (1 if o['side'] == 'sell' else -1)
                         for o in orders if o['period_index'] == t),
        clearing_price_eur_mwh=result.dam['prices_eur_mwh'][t])
        for t, at in enumerate(times)]
    schedules = {(m['asset'], m['period']): m for m in result.meters if m['asset'] in assets}
    for order in orders:
        order['market_schedule_mwh'] = schedules[order['asset_id'], order['period_index']]['MSQ_mwh']
    active = {r['period']: r['isp_run'] for r in result.rtbm}
    runs = []
    for run in result.isp_runs:
        records = []
        for row in run['rows']:
            for asset in sorted(assets & row['assets'].keys()):
                record = _asset_interval(row['assets'][asset], asset, _interval(row['at'], row['hours'], row['period']))
                record.update(effective_for_dispatch=active.get(row['period']) == run['id'],
                              market_schedule_mwh=schedules[asset, row['period']]['MSQ_mwh'],
                              reserved_capacity_mw=deepcopy(row['assets'][asset]['reserve_mw']))
                record['scheduled_energy_mwh'] = record.pop('instructed_energy_mwh')
                record['dispatch_power_mw'] = record.pop('average_power_mw')
                record.pop('ending_power_mw', None)
                records.append(record)
            for asset in sorted(assets - row['assets'].keys()):
                records.append(dict(_interval(row['at'], row['hours'], row['period']),
                    asset_id=asset, market_schedule_mwh=schedules[asset, row['period']]['MSQ_mwh'],
                    effective_for_dispatch=active.get(row['period']) == run['id']))
        runs.append(dict(run_id=run['id'], intervals=records))
    stages['isp'] = dict(runs=runs)
    records = []
    for i, row in enumerate(result.rtbm):
        for asset in sorted(assets & row['assets'].keys()):
            record = _asset_interval(row['assets'][asset], asset, _interval(row['at'], row['hours'], row['period']))
            record['isp_run_id'] = row['isp_run']
            if asset in row['RES_delivery_mwh']:
                record['actual_energy_mwh'] = row['RES_delivery_mwh'][asset]
            records.append(record)
        for asset in sorted(assets - row['assets'].keys()):
            actual = result.inputs['actual_mw'][asset][i] * row['hours']
            records.append(dict(_interval(row['at'], row['hours'], row['period']), asset_id=asset,
                market_schedule_mwh=schedules[asset, row['period']]['MSQ_mwh'] / 6,
                actual_consumption_mwh=actual, isp_run_id=row['isp_run']))
    stages['rtbm'] = dict(intervals=records)
    financials = {}
    for stage, codes in (('forward_market', {'S01'}), ('day_ahead_market', {'S02'}),
                         ('settlement', set(CATALOGUE) - {'S01', 'S02'})):
        entries = [e for e in result.ledger if e['component'] in codes]
        financials[stage] = _cash_totals(entries, participant)
        stages[stage]['cash_by_component'] = financials[stage]
        stages[stage]['cash_eur'] = str(sum((Decimal(v['cash_eur']) for v in financials[stage].values()), Decimal(0)))
        stages[stage]['cash_postings'] = _cash_lines(entries, participant)
    meters = []
    asset_owners = {a['id']: a['participant'] for a in result.inputs['assets']}
    balance_owners = {p['id']: p['brp'] or p['id'] for p in result.inputs['participants']}
    for meter in result.meters:
        owner = asset_owners[meter['asset']]
        if meter['asset'] not in assets and balance_owners[owner] != participant:
            continue
        meters.append(dict(_interval(times[meter['period']], .5, meter['period']), asset_id=meter['asset'],
            asset_owner=owner, balancing_responsibility=balance_owners[owner] == participant,
            market_schedule_mwh=meter['MSQ_mwh'], metered_energy_mwh=meter['actual_mwh'],
            instructed_imbalance_mwh=meter['INSTQ_mwh'], uninstructed_imbalance_mwh=meter['UNINSTQ_mwh']))
    stages['settlement'].update(meters=meters,
        financial_position=_financial_position(result, participant),
        total_participant_cash_eur=str(sum((Decimal(stages[s]['cash_eur'])
            for s in ('forward_market', 'day_ahead_market', 'settlement')), Decimal(0))),
        ending_states={a: deepcopy(s) for a, s in result.ending_states.items() if a in assets})
    return stages


def readable_results(result, *, audience='shared', participant=None):
    """Shared fields are allowlisted. Private views require an explicit audience."""
    ids = [p['id'] for p in result.inputs['participants']]
    if audience not in ('shared', 'participant', 'operator'):
        raise ValueError('Audience must be shared, participant or operator')
    if audience == 'participant' and participant not in ids:
        raise ValueError('Unknown participant')
    if audience != 'participant' and participant is not None:
        raise ValueError('Participant ID is only valid for a participant view')
    stages = _shared(result)
    if audience != 'shared':
        selected = ids if audience == 'operator' else [participant]
        for name, stage in stages.items():
            if name == 'isp':
                for run in stage['runs']:
                    run['participants'] = {}
            else:
                stage['participants'] = {}
        for owner in selected:
            for name, values in _participant(result, owner).items():
                if name == 'isp':
                    by_run = {run['run_id']: run for run in values['runs']}
                    for run in stages[name]['runs']:
                        run['participants'][owner] = dict(intervals=by_run[run['run_id']]['intervals'])
                else:
                    stages[name]['participants'][owner] = values
    day = result.manifest['day']
    return dict(schema_version='1.0', audience=audience,
        cash_convention='positive_receipt_negative_payment',
        simulation_summary=dict(period_start=day, period_end=day,
            days=[dict(date=day, summary=dict(audit_passed=result.audit['passed'],
                trading_intervals=len(result.inputs['time_index']), rtbm_intervals=len(result.rtbm),
                total_demand_mwh=sum(r['actual_demand_mwh'] for r in result.rtbm),
                total_res_generation_mwh=sum(r['actual_RES_mwh'] for r in result.rtbm)),
                market_stages=stages)]))


def save_readable_results(result, directory):
    """Write an operator report, shared summaries and one report per participant."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = dict(operator=directory/'readable_results.json', shared=directory/'shared_results.json', participants={})
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    write(paths['operator'], readable_results(result, audience='operator'))
    write(paths['shared'], readable_results(result))
    for owner in (p['id'] for p in result.inputs['participants']):
        filename = 'participant_' + hashlib.sha256(owner.encode()).hexdigest()[:16] + '.json'
        path = directory/'private'/filename
        write(path, readable_results(result, audience='participant', participant=owner))
        paths['participants'][owner] = path
    return paths

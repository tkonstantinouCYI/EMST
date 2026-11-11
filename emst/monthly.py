"""Close a complete month of public daily results and reconcile corrected meters."""
from calendar import monthrange
from datetime import date

from .accounting import (
    finalise_accounts, monthly_calendar, period_recovery, post_tariffs,
    reconcile_meter_ledgers, reconciliation_calendar,
)
from .calendar import day_intervals
from .compliance import systematic
from .settlement import D, Ledger, instructed_cash, offtake_bridge, res_bridge, statement


def _copy_posting(ledger, entry, prefix, *, cash=None, quantity=None):
    ledger.post(prefix + entry['id'], entry['component'], entry['account'],
                entry['counterparty'], entry['quantity'] if quantity is None else quantity,
                entry['price'], entry['period'], entry['rule'], entry['source'],
                cash=entry['cash_eur'] if cash is None else cash)


def _month_ledger(days, month, parameters, corrections, penalty_rates, assessments=None):
    ledger = Ledger()
    consumption = {}
    annex_rows = []
    removed_accruals = []
    statistical = {}
    expected_periods = []
    for day, result in days.items():
        periods = day_intervals(date.fromisoformat(day), 30)
        assets = {asset['id']: asset for asset in result.inputs['assets']}
        owners = {p['id']: p['brp'] or p['id'] for p in result.inputs['participants']}
        rules = result.manifest['ruleset']['settlement']
        tariffs = result.inputs['settlement_parameters']['tariffs']
        prefix = day + '/'
        postings = {entry['id']: entry for entry in result.ledger}
        grouped = {p.id: [] for p in periods}
        for entry in result.ledger:
            if entry['component'] in ('S20','S21','S22'):
                raise ValueError('Daily results contain excluded UA5/UA6/UA7 charges; rerun with the current public API')
            if entry['component'] in ('S18', 'S19') and entry['rule'] == 'daily_accrual':
                removed_accruals.append({**entry, 'id': prefix + entry['id']})
            elif entry['period'] in grouped:
                grouped[entry['period']].append(entry)
            else:
                _copy_posting(ledger, entry, prefix)
        meters = {p.period: [] for p in periods}
        for row in result.meters:
            meters[row['period']].append(row)
        for period in periods:
            expected_periods.append(period.id)
            rows = meters[period.period]
            changed = any((day, period.period, r['asset']) in corrections for r in rows)
            native, deviations, parties, tariff_rows, updates = {}, {}, {}, [], {}
            batch = result.rtbm[period.period * 6:period.period * 6 + 6]
            for row in rows:
                asset = assets[row['asset']]
                technology, config = asset['technology'], asset['parameters']
                participant = asset['participant']
                key = day, period.period, row['asset']
                meter = corrections.get(key, row['actual_mwh'])
                inst = row['INSTQ_mwh']
                uninst = row['UNINSTQ_mwh']
                schedule = row['MSQ_mwh']
                suffix = f"{row['asset']}:{period.period}"
                price = postings['uninst:' + suffix]['price']
                if key in corrections:
                    if technology == 'supplier':
                        tolerance = rules['small_supplier_tolerance'] if config['small_supplier'] else None
                        uninst = offtake_bridge(schedule, meter, tolerance=tolerance)['UNINSTQ']
                    elif technology == 'res':
                        down = sum(r['RES_delivered_down_mwh'][asset['id']] for r in batch)
                        bridge = res_bridge(schedule, down, meter, rules, config.get('grant_res', False))
                        inst, uninst = bridge['INSTQ'], bridge['UNINSTQ']
                    else:
                        uninst = meter - schedule - inst
                    if inst != row['INSTQ_mwh'] and not config.get('grant_res'):
                        old = postings['inst:' + suffix]
                        offers = [x for r in batch for x in r['assets'].values()]
                        net = [sum(x['up_mwh'] - x['down_mwh'] for x in r['assets'].values()
                                   if x.get('reason', 'ordinary_market') in ('ordinary_market', 'RES_down_offer')) for r in batch]
                        offset = sum(r['assets'][asset['id']]['up_mwh'] - r['assets'][asset['id']]['down_mwh']
                                     for r, n in zip(batch, net) if abs(n) < 1e-7)
                        up = max((x['up_offer_eur_mwh'] for x in offers if x['up_mwh'] > 1e-7), default=float(price))
                        down = min((x['down_offer_eur_mwh'] for x in offers if x['down_mwh'] > 1e-7), default=float(price))
                        system = 'short' if sum(net) >= 0 else 'long'
                        args = system, float(price), float(price), up, down
                        delta = instructed_cash(inst - offset, *args) - instructed_cash(row['INSTQ_mwh'] - offset, *args)
                        updates[old['id']] = (inst, D(old['cash_eur']) + D(delta))
                    updates['uninst:' + suffix] = (uninst, D(uninst) * D(price))
                if technology == 'supplier':
                    native[participant] = native.get(participant, 0.) + meter
                    consumption[participant] = consumption.get(participant, D(0)) + D(meter)
                    tariff_rows.append(dict(id=suffix, participant=participant, period=period.id,
                        energy_mwh=meter, hours=period.hours, voltage=config['voltage'], purpose='consumption'))
                deviations[asset['id']] = uninst
                parties[asset['id']] = owners[participant]
                if assessments is None and asset['id'] in penalty_rates:
                    if technology not in ('supplier', 'res'):
                        raise ValueError('Systematic-deviation rates require a supplier or RES asset')
                    role = 'load' if technology == 'supplier' else 'RES'
                    down = 0 if role == 'load' else sum(r['RES_delivered_down_mwh'][asset['id']] for r in batch)
                    statistical.setdefault(asset['id'], (participant, role, []))[2].append(dict(
                        period=period.id, schedule_mwh=schedule, meter_mwh=meter,
                        adjusted_schedule_mwh=meter + uninst, down_mwh=down))
                annex_rows.append(dict(id=prefix + suffix, participant=owners[participant],
                    period=period.id, role='offtake' if technology == 'supplier' else 'production',
                    meter_mwh=meter, price_eur_mwh=price))
            first = len(ledger.entries)
            for entry in grouped[period.id]:
                if changed and entry['component'] in ('S14','S15','S16','S17','S20','S21','S22'):
                    continue
                quantity, cash = updates.get(entry['id'], (None, None))
                _copy_posting(ledger, entry, prefix, cash=cash, quantity=quantity)
            if changed:
                # Use the same IDs as daily settlement after adding the day namespace.
                recovery = Ledger()
                post_tariffs(recovery, tariff_rows, tariffs, include_levies=False)
                period_recovery(recovery, period.id, native, deviations, parties,
                                'qualified_consumption_fallback', ledger.entries[first:])
                for entry in recovery.export():
                    _copy_posting(ledger, entry, prefix)
    if assessments is None:
        if penalty_rates.keys() - statistical.keys():
            raise ValueError('Unknown asset in systematic-deviation rates')
        assessments = {asset: dict(participant=participant,
            **systematic(records, expected_periods, role, penalty_rates[asset]))
            for asset, (participant, role, records) in statistical.items()}
    for asset, assessment in assessments.items():
        ledger.post(f'monthly_systematic:{asset}', 'S23',
            f"participant:{assessment['participant']}:nonDAM", 'A-E',
            -D(assessment['amount_eur']), 1, month, assessment['formula_id'], 'complete_month_meter_history')
    finalise_accounts(ledger, month, consumption, None, None, parameters['penalty_shares'], uplift_scope='UA1_UA2_only')
    return ledger, annex_rows, removed_accruals, assessments


def settle_month(results, month, *, penalty_shares=None, reserve_cost_eur=0, reconciliation_cost_eur=0,
                  meter_corrections=None, systematic_penalty_rates=None, holidays=()):
    """Close a calendar month and optionally produce separate Annex adjustments.

    Corrections map (ISO day, zero-based MTU, asset ID) to loss-adjusted MWh.
    Storage values use signed injection. All other meters use positive MWh.
    """
    start = date.fromisoformat(month + '-01')
    expected = [start.replace(day=i).isoformat() for i in range(1, monthrange(start.year, start.month)[1]+1)]
    missing = [day for day in expected if day not in results.days]
    if missing:
        raise ValueError(f'Incomplete settlement month {month}: missing {", ".join(missing)}')
    days = {day: results.days[day] for day in expected}
    if any(not r.audit['passed'] or r.manifest['day'] != day for day, r in days.items()):
        raise ValueError('Monthly closure requires audited results for the requested dates')
    corrections = {}
    for (day, mtu, asset), value in (meter_corrections or {}).items():
        if day not in days or not any(r['period'] == mtu and r['asset'] == asset for r in days[day].meters):
            raise ValueError(f'Unknown reconciliation meter {(day, mtu, asset)}')
        meter = D(value)
        technology = next(a['technology'] for a in days[day].inputs['assets'] if a['id'] == asset)
        if not meter.is_finite() or (technology != 'storage' and meter < 0):
            raise ValueError(f'Invalid reconciliation meter {(day, mtu, asset)}')
        corrections[day, mtu, asset] = float(meter)
    if D(reserve_cost_eur) or D(reconciliation_cost_eur):
        raise ValueError('Public settlement excludes UA-3 and UA-4')
    # Accept the old zero-valued destination keys for call compatibility only;
    # they never create UA6/UA7 ledger postings in public settlement.
    shares = {'UA-1': 1} if penalty_shares is None else dict(penalty_shares)
    for key in ('UA-6', 'UA-7'):
        if key in shares and D(shares[key]) == 0: del shares[key]
    if set(shares) != {'UA-1'} or D(shares['UA-1']) != 1:
        raise ValueError('Public settlement supports only 100% penalty redistribution to UA1')
    parameters = dict(penalty_shares=shares)
    rates = systematic_penalty_rates or {}
    original, inputs, accruals, assessments = _month_ledger(days, month, parameters, {}, rates)
    holidays = frozenset(date.fromisoformat(h) if isinstance(h, str) else h for h in holidays)
    output = dict(monthly=statement(original, 'monthly', month, expected, list(days)),
                  calendar=monthly_calendar(start.year, start.month, holidays), ledger=original.export(),
                  replaced_daily_accruals=accruals, systematic_penalties=assessments,
                  excluded_uplifts=['UA-3', 'UA-4', 'UA-5', 'UA-6', 'UA-7'], reconciliation=None)
    if meter_corrections is not None:
        recomputed, revised, _, _ = _month_ledger(days, month, parameters, corrections, rates, assessments)
        ledger, adjustments, delta, replay = reconcile_meter_ledgers(inputs, revised, original, recomputed)
        output['reconciliation'] = dict(ledger=ledger.export(), energy_adjustments=adjustments,
            related_account_delta_eur=str(delta), replay_delta_eur=str(replay),
            calendar=reconciliation_calendar(start.year, start.month, holidays))
    return output

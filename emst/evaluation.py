"""Planning diagnostics and accounting, separate from market objectives/transfers."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, localcontext
import hashlib,json,math


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def residuals(rows,tolerance_mwh=1e-6):
    if isinstance(tolerance_mwh,bool) or not math.isfinite(tolerance_mwh) or tolerance_mwh<0:
        raise ValueError('actual_balance_tolerance_mwh must be finite nonnegative')
    values=[r['physical_imbalance_mwh'] for r in rows]
    return dict(sign_convention='positive_observed_demand_exceeds_delivered_net_injection',
        tolerance_mwh=tolerance_mwh,positive_energy_mwh=sum(max(v,0) for v in values),
        negative_energy_magnitude_mwh=sum(max(-v,0) for v in values),net_energy_mwh=sum(values),
        maximum_positive_mw=max((max(v,0)/r['hours'] for v,r in zip(values,rows)),default=0),
        maximum_negative_mw=max((max(-v,0)/r['hours'] for v,r in zip(values,rows)),default=0),
        maximum_absolute_mwh=max((abs(v) for v in values),default=0),
        positive_interval_count=sum(v>tolerance_mwh for v in values),
        negative_interval_count=sum(v < -tolerance_mwh for v in values),
        actual_delivered_balance=all(abs(v)<=tolerance_mwh for v in values),
        reliability_interpretation_available=False,load_loss_reliability='unassessed',
        physical_recourse_model='none',EENS_mwh=None,LOLE_hours=None)


def economics(result):
    """Attribute only explicit asset-specific postings; never invent shared allocations."""
    assets={a['id']:a for a in result.inputs['assets']}
    mapping={}
    for order in result.orders:mapping['DAM:'+order['id']]={'account':order['asset']}
    for c in result.inputs.get('forward_contracts',[]):
        loads=[a for a in assets.values() if a['participant']==c['buyer'] and a['technology']=='supplier']
        target=c.get('buyer_entity') or (loads[0]['id'] if len(loads)==1 else None)
        for t in range(48):mapping[f'forward:{c["id"]}:{t}']={'account':c['asset'],'counterparty':target}
    for aid in assets:
        for t in range(48):
            for prefix in ('inst','uninst','startup'):
                mapping[f'{prefix}:{aid}:{t}']={'account':aid}
            for code in ('S14','S15'):mapping[f'tariff:{aid}:{t}:{code}']={'account':aid}
            for product in result.inputs['reserve_requirements_mw']:
                mapping[f'reserve:{aid}:{t}:{product}']={'account':aid}
            for prefix in ('service','energy'):
                mapping[f'must_run:{prefix}:{aid}:{t}']={'account':aid}
    cash={aid:{} for aid in assets};unallocated={p['id']:Decimal(0) for p in result.inputs['participants']}
    participant_totals={p:Decimal(0) for p in unallocated}
    with localcontext() as context:
        context.prec=80
        for entry in result.ledger:
            for leg,sign in (('account',1),('counterparty',-1)):
                account=entry[leg]
                if not account.startswith('participant:'):continue
                owner=account.split(':')[1];amount=Decimal(entry['cash_eur'])*sign
                participant_totals[owner]+=amount
                aid=mapping.get(entry['id'],{}).get(leg)
                if aid not in assets:unallocated[owner]+=amount
                else:cash[aid][owner]=cash[aid].get(owner,Decimal(0))+amount
        output={}
        for aid,asset in assets.items():
            p=asset['parameters'];technology=asset['technology'];intervals=[]
            for r in result.rtbm:
                x=r['assets'].get(aid,{})
                variable=(x.get('actual_mwh',0)*x.get('effective_marginal_cost',p['marginal_cost'])) if technology=='thermal' else 0.
                transition=x.get('transition_cost_eur',0)
                startup=x.get('start',0)*p.get('startup_cost',0)
                no_load=x.get('on',0)*r['hours']*x.get('effective_no_load_cost_per_hour',p.get('no_load_cost_per_hour',0))
                throughput=x.get('charge_mwh',0)+x.get('discharge_mwh',0)
                degradation=throughput*p.get('degradation_cost',0)
                intervals.append(dict(at=r['at'],period_index=r['period'],hours=r['hours'],
                    variable_cost_eur=variable,startup_cost_eur=startup,no_load_cost_eur=no_load,
                    throughput_mwh=throughput,degradation_cost_eur=degradation,
                    **({'transition_cost_eur':transition} if 'plant_configurations' in p else {}),
                    total_operating_cost_eur=variable+startup+no_load+degradation+transition))
            fields=('variable_cost_eur','startup_cost_eur','no_load_cost_eur','throughput_mwh','degradation_cost_eur','total_operating_cost_eur')
            if 'plant_configurations' in p:fields=fields+('transition_cost_eur',)
            total=sum(cash[aid].values(),Decimal(0))
            output[aid]=dict(participant=asset['participant'],technology=technology,
                attributed_cash_by_settlement_participant_eur={p:str(v) for p,v in cash[aid].items()},
                attributable_net_cash_eur=str(total),physical_costs=dict(intervals=intervals,
                    totals={k:sum(r[k] for r in intervals) for k in fields}),
                contribution_before_shared_charges_eur=str(total-Decimal(str(sum(r['total_operating_cost_eur'] for r in intervals)))),
                contribution_is_final_profit=False)
        reconciliation={}
        for owner in participant_totals:
            attributed=sum((values.get(owner,Decimal(0)) for values in cash.values()),Decimal(0))
            reconciliation[owner]=dict(attributed_asset_cash_eur=str(attributed),
                unallocated_participant_cash_eur=str(unallocated[owner]),participant_total_cash_eur=str(participant_totals[owner]),
                reconciled=attributed+unallocated[owner]==participant_totals[owner])
    return dict(assets=output,participants=reconciliation,
        physical_operating_cost_eur=sum(a['physical_costs']['totals']['total_operating_cost_eur'] for a in output.values()),
        conventions=dict(storage_throughput='charge_MWh_plus_discharge_MWh_at_grid',
            variable_cost='thermal_delivered_MWh_times_marginal_cost',startup='executed_RTBM_start_events',
            no_load='online_including_transition_hours',shared_charges='unallocated',
            excluded_costs=['capital','fixed_OM','shutdown_cost_not_modelled','unserved_energy_not_modelled'],
            market_payments_are_transfers=True,asset_attribution_does_not_transfer_BRP_cash=True))


def enrich(result,market,records,tolerance_mwh):
    result.inputs['actual_balance_tolerance_mwh']=tolerance_mwh
    result.inputs['solver_config']=deepcopy(market.solver_config)
    result.inputs['forward_contracts']=[asdict(c) for c in market.forwards]
    balance=residuals(result.rtbm,tolerance_mwh)
    stage_times={}
    for r in records:stage_times[r['stage']]=stage_times.get(r['stage'],0)+r['wall_seconds']
    result.diagnostics=dict(solve_records=deepcopy(records),solver_wall_seconds_by_stage=stage_times,
        actual_balance=balance,optimization_feasible=True,
        forecast_balance_verified=result.audit['checks'].get('forecast_RTBM_balance',False),
        forecast_clearing_without_slack=result.manifest['physical_security_feasible'],
        smoothing_fallback_count=sum(r.get('smoothing',{}).get('status')=='fallback_primary' for r in result.rtbm))
    result.economics=economics(result)
    owners={p.id:p.economic_owner or p.id for p in market.participants.values()}
    result.economics['economic_owners']={owner:dict(participants=sorted(p for p,v in owners.items() if v==owner),
        net_cash_eur=str(sum((Decimal(d['participant_total_cash_eur']) for p,d in result.economics['participants'].items() if owners[p]==owner),Decimal(0)))) for owner in sorted(set(owners.values()))}
    result.manifest.update(result_schema_version='1.1',evaluation_schema_version='1.0',
        execution_mode='independent_day',actual_load_loss_reliability='unassessed',
        actual_delivered_balance=balance['actual_delivered_balance'],
        physical_security_feasible_semantics='forecast_clearing_without_optimization_slack',
        input_fingerprint=fingerprint(result.inputs),policy_fingerprint=fingerprint([dict(asset=a['id'],strategy=a['strategy']) for a in result.inputs['assets']]),implementation_fingerprint=fingerprint(result.manifest['code_sha256']))
    return result


def evaluation_result(result):
    return dict(schema_version='1.0',status='ok' if result.audit['passed'] else 'audit_failed',
        manifest=deepcopy(result.manifest),diagnostics=deepcopy(result.diagnostics),
        economics=deepcopy(result.economics),initial_states={a['id']:a['initial_state'] for a in result.inputs['assets'] if a['initial_state'] is not None},
        ending_states=deepcopy(result.ending_states),audit=deepcopy(result.audit))

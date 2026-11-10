"""Source-based tariff, uplift and recalculation primitives (13.6-13.12)."""
from dataclasses import dataclass
from datetime import date,timedelta
from calendar import monthrange
from .settlement import D,Ledger,recover,statement
from .calendar import next_business_day


def tariff_lines(meter,tariffs):
    """Native consumption meters; power quote EUR/MW/period converted once."""
    q=D(meter["energy_mwh"]);dt=D(meter["hours"])
    voltage=meter["voltage"]
    if q<0 or dt<=0 or voltage not in ("HV","MV","LV"):
        raise ValueError("Invalid tariff meter")
    if meter["purpose"]=="eligible_storage_charging":
        return {s:D(0) for s in ("S14","S15","S20","S21","S22")}
    if meter["purpose"] not in ("consumption","auxiliary","supplier_business"):
        raise ValueError("Missing meter-purpose eligibility")
    row=tariffs[voltage]
    tuos=q*D(row["tuos_energy"])+q/dt*D(row["tuos_power"])
    duos=D(0)
    if voltage in ("MV","LV"):
        duos=q*D(tariffs["MV"]["duos_energy"])+q/dt*D(tariffs["MV"]["duos_power"])
    if voltage=="LV":
        duos+=q*D(tariffs["LV"]["duos_energy"])+q/dt*D(tariffs["LV"]["duos_power"])
    return {"S14":tuos,"S15":duos,"S20":q*D(row["RES"]),
            "S21":q*D(row["PSO"]),"S22":q*D(row["admin"])}


def allocate(amount,base):
    amount=D(amount);weights={k:D(v) for k,v in base.items()}
    if any(v<0 for v in weights.values()):raise ValueError("Negative allocation weight")
    total=sum(weights.values(),D(0))
    if not total:
        if amount:raise ValueError("Positive cost has no eligible allocation base")
        return {k:D(0) for k in weights}
    result={k:amount*v/total for k,v in weights.items()}
    last=next(reversed(result))
    result[last]+=amount-sum(result.values(),D(0))
    return result


def ancillary_recovery(cost1,cost2,consumption,entity_deviations,entity_participants,zero_policy):
    """13.8.3: FCR/RR/contract/start costs by load; aFRR/mFRR by abs UNINSTQ."""
    first=allocate(cost1,consumption)
    weights={e:abs(D(v)) for e,v in entity_deviations.items()}
    if not sum(weights.values(),D(0)) and D(cost2):
        if zero_policy!="qualified_consumption_fallback":
            raise ValueError("Zero aggregate UNINSTQ with nonzero aFRR/mFRR cost")
        second=allocate(cost2,consumption)
        convention="zero_deviation_recovery_consumption_v1"
    else:
        entity_amount=allocate(cost2,weights)
        second={}
        for e,amount in entity_amount.items():
            p=entity_participants[e];second[p]=second.get(p,D(0))+amount
        convention=None
    combined=dict(first)
    for p,q in second.items():combined[p]=combined.get(p,D(0))+q
    return combined,convention


def post_tariffs(ledger,meters,tariffs):
    accounts={"S14":"A-F","S15":"A-G","S20":"UA-5","S21":"UA-6","S22":"UA-7"}
    result=[]
    for meter in meters:
        charges=tariff_lines(meter,tariffs)
        for component,amount in charges.items():
            ledger.post(f"tariff:{meter['id']}:{component}",component,
                f"participant:{meter['participant']}:nonDAM",accounts[component],1,1,
                meter["period"],"13.6-8/MS6.1.4-7",meter["id"],cash=-amount)
        result.append({"meter":meter["id"],"amounts_eur":{k:str(v) for k,v in charges.items()},
                       "eligibility":meter["purpose"]})
    return result


def period_recovery(ledger,period,consumption,bridges,entity_participants,zero_policy,entries=None):
    entries=[e for e in ledger.entries if e.period==str(period)] if entries is None else entries
    imbalance=sum((D(e.cash_eur) for e in entries if e.component in ("S03","S04","S12")),D(0))
    cost1=sum((D(e.cash_eur) for e in entries if e.component in ("S05","S08","S09","S10","S11","S13")),D(0))
    cost2=sum((D(e.cash_eur) for e in entries if e.component in ("S06","S07")),D(0))
    # Retain the account transfers rather than collapsing the seven uplift accounts.
    for component,account,operator,cost in (("S16","UA-1","A-B",imbalance),("S17","UA-2","A-C",cost1+cost2)):
        ledger.post(f"transfer:{component}:{period}",component,operator,account,1,1,period,
                    "13.8.2-3","period_cost_aggregation",cash=cost)
    for p,amount in allocate(imbalance,consumption).items():
        ledger.post(f"UA1:{period}:{p}","S16",f"participant:{p}:nonDAM","UA-1",1,1,period,
                    "13.8.2.3","native_loss_adjusted_meter",cash=-amount)
    charges,convention=ancillary_recovery(cost1,cost2,consumption,bridges,entity_participants,zero_policy)
    for p,amount in charges.items():
        ledger.post(f"UA2:{period}:{p}","S17",f"participant:{p}:nonDAM","UA-2",1,1,period,
                    "13.8.3.3-5","consumption_and_abs_UNINSTQ",cash=-amount)
    return {"period":period,"imbalance_residual_eur":str(imbalance),"ANSCC1":str(cost1),
            "ANSCC2":str(cost2),"convention":convention}


def close_month_accounts(ledger,period,consumption,reserve_cost,reconciliation_cost,penalty_shares,external):
    """13.8.4/5 and R08 distribution; supplied external costs are explicit inputs."""
    if sum(map(D,penalty_shares.values()),D(0))!=1 or set(penalty_shares)!={"UA-1","UA-6","UA-7"}:
        raise ValueError("Dated R08 noncompliance redistribution shares must sum to one")
    penalty=ledger.balances().get("A-E",D(0))
    for target,share in penalty_shares.items():
        ledger.post(f"penalty_distribution:{period}:{target}","S23",target,"A-E",1,1,period,
                    "13.2.6.2/13.9.17/R08","approved_split_synthetic_values",cash=penalty*D(share))
    # UA1 penalty allocation is a separate monthly credit to consumers; do not
    # silently revalue each period's already-settled marginal energy.
    for p,amount in allocate(penalty*D(penalty_shares["UA-1"]),consumption).items():
        ledger.post(f"UA1_penalty_credit:{period}:{p}","S16",f"participant:{p}:nonDAM","UA-1",1,1,period,
                    "13.8.2.5/R08","monthly_consumption",cash=amount)
    for component,account,operator,cost in (("S18","UA-3","A-D",reserve_cost),
                                            ("S19","UA-4","A-H",reconciliation_cost)):
        if cost is None:
            continue
        ledger.post(f"monthly_cost:{component}:{period}",component,operator,account,1,1,period,
                    "13.8.4-5","dated_external_cost",cash=D(cost))
        for p,amount in allocate(cost,consumption).items():
            ledger.post(f"monthly_recovery:{component}:{period}:{p}",component,
                        f"participant:{p}:nonDAM",account,1,1,period,"13.8.4-5","monthly_consumption",cash=-amount)
    for account,beneficiary in external.items():
        amount=ledger.balances().get(account,D(0))
        ledger.post(f"external_transfer:{period}:{account}","S22" if account=="UA-7" else "S21" if account=="UA-6" else "S20" if account=="UA-5" else "S14" if account=="A-F" else "S15",
                    beneficiary,account,1,1,period,"13.6-8","account_close_external_counterparty",cash=amount)


class Recalculation:
    """Append-only difference of independently recalculated complete ledgers.

    The same mechanism serves distinct cumulative and meter-reconciliation runs;
    the calculator, scope and exception set remain explicit for each operation.
    """
    def __init__(self):self.applied={}

    def apply(self,target,old,new,revision,kind,frozen_components=frozenset()):
        if kind not in ("cumulative","meter_reconciliation"):raise ValueError("Unknown revision scope")
        import hashlib,json
        fingerprint=hashlib.sha256(json.dumps(new.export(),sort_keys=True).encode()).hexdigest()
        key=kind,revision
        if key in self.applied:
            if self.applied[key]!=fingerprint:raise ValueError("Revision ID has conflicting recalculation")
            return D(0)
        if kind=="meter_reconciliation":frozen_components=set(frozen_components)|{"S01","S02","S24","S25","S26"}
        old_by={e.id:e for e in old.entries};new_by={e.id:e for e in new.entries}
        delta=D(0)
        for id in sorted(set(old_by)|set(new_by)):
            prior,current=old_by.get(id),new_by.get(id)
            template=current or prior
            if template.component in frozen_components:continue
            if prior and current and (prior.account,prior.counterparty)!=(current.account,current.counterparty):
                for label,e,multiplier in (("representation_reversal",prior,-1),("representation_reallocation",current,1)):
                    target.post(f"{kind}:{revision}:{label}:{id}","S30" if kind=="cumulative" else "S31",
                        e.account,e.counterparty,1,1,e.period,"13.12" if kind=="cumulative" else "13.11/Annex13.1",
                        f"{label}:{e.component}:{id}",revision=revision,cash=D(e.cash_eur)*multiplier)
                delta+=D(current.cash_eur)-D(prior.cash_eur)
                continue
            amount=(D(current.cash_eur) if current else D(0))-(D(prior.cash_eur) if prior else D(0))
            if not amount:continue
            target.post(f"{kind}:{revision}:{id}","S30" if kind=="cumulative" else "S31",
                        template.account,template.counterparty,1,1,template.period,
                        "13.12" if kind=="cumulative" else "13.11/Annex13.1",
                        f"recompute:{template.component}:{id}",revision=revision,cash=amount)
            delta+=amount
        self.applied[key]=fingerprint
        return delta


def reconcile_profiles(total_meter_mwh,nonprofiled,profile_weights,loss_multiplier):
    from .metering import allocate_profile
    remainder=D(total_meter_mwh)-sum(map(D,nonprofiled.values()),D(0))
    profiles=allocate_profile(remainder,profile_weights)
    return {**{k:D(v)*D(loss_multiplier) for k,v in nonprofiled.items()},
            **{k:v*D(loss_multiplier) for k,v in profiles.items()}}


def business_offset(day,number,holidays):
    for _ in range(number):day=next_business_day(day,holidays)
    return day


def monthly_calendar(year,month,holidays,kind="standard"):
    next_month=date(year+int(month==12),1 if month==12 else month+1,14)
    if kind=="MM":
        anchor=next_month.replace(day=1)
        return {"calculation_start":anchor.isoformat(),"preliminary":business_offset(anchor,2,holidays).isoformat(),
                "objection_deadline":business_offset(anchor,7,holidays).isoformat(),
                "final":business_offset(anchor,11,holidays).isoformat(),
                "invoice":business_offset(anchor,12,holidays).isoformat(),"rule":"MM6 monthly timetable; FC cash cycle overlay"}
    if kind!="standard":raise ValueError("Unknown monthly calendar class")
    return {"calculation_start":next_month.isoformat(),
            "preliminary":business_offset(next_month,2,holidays).isoformat(),
            "objection_deadline":business_offset(next_month,7,holidays).isoformat(),
            "final":business_offset(next_month,7,holidays).isoformat(),
            "rule":"13.10.1.2-3; clearing/payment times supplied separately"}


def annual_reserve_rebate(recovered_defaults,ytd_contributions):
    """13.8.4.3: allocate recovered defaulter money by annual contributions."""
    return allocate(recovered_defaults,ytd_contributions)


def annex_energy_cash(old_rows,new_rows):
    """Annex 13.1.7: signed generator-point quantity change times historical IMSP.

    Native positive consumption is adapted once to injection-positive cash. Both
    profiles and ordinary meters must already have their loss/allocation bridge.
    """
    old={r["id"]:r for r in old_rows}
    result=[]
    for r in new_rows:
        prior=old[r["id"]]
        q=(D(r["meter_mwh"])-D(prior["meter_mwh"]))*(1 if r["role"]=="production" else -1)
        if not q:continue
        result.append(dict(id=r["id"],participant=r["participant"],period=r["period"],
            signed_delta_mwh=str(q),historical_IMSP=str(prior["price_eur_mwh"]),
            cash_eur=str(q*D(prior["price_eur_mwh"])),source_clause="Annex13.1.7.1.4/7.2.5"))
    return result


def reconciliation_calendar(year,month,holidays):
    number=year*12+month-1+3
    submitted=date(number//12,number%12+1,15)
    preliminary=business_offset(submitted,5,holidays)
    return dict(quantity_submission=submitted.isoformat(),preliminary=preliminary.isoformat(),
        objection_deadline=business_offset(preliminary,5,holidays).isoformat(),
        final=business_offset(preliminary,10,holidays).isoformat(),rule="Annex13.1.7/8")


def finalise_accounts(ledger,month,consumption,reserve_cost,reconciliation_cost,penalty_shares):
    """Post assessed external costs and close the common monthly accounts."""
    for account,cost,beneficiary,component in (("A-D",reserve_cost,"clearing_service_provider","S18"),
                                              ("A-H",reconciliation_cost,"prior_reconciliation_counterparties","S19")):
        if cost is None:
            continue
        ledger.post(f"external_cost:{month}:{account}",component,beneficiary,account,1,1,month,
                    "13.8.4-5","supplied_assessed_external_cost",cash=cost)
    close_month_accounts(ledger,month,consumption,reserve_cost,reconciliation_cost,penalty_shares,
        {"A-F":"transmission_owner","A-G":"distribution_owner","UA-5":"RES_fund","UA-6":"PSO","UA-7":"TSOC"})


def reconcile_meter_ledgers(inputs,revised,original,recomputed):
    """Apply Annex energy adjustments and related account differences."""
    revision_ledger=Ledger();engine=Recalculation()
    energy_adjustments=annex_energy_cash(inputs,revised)
    for adjustment in energy_adjustments:
        revision_ledger.post(f"Annex_energy:{adjustment['id']}","S31",f"participant:{adjustment['participant']}:nonDAM",
            "A-H",adjustment["signed_delta_mwh"],adjustment["historical_IMSP"],adjustment["period"],
            adjustment["source_clause"],"certified_meter_and_profile_revision")
    def related_accounts(source):
        target=Ledger()
        for e in source.entries:
            if e.component not in {"S14","S15","S16","S17","S18","S19","S20","S21","S22","S28"}:continue
            # Related-account adjustment funding is reconciled via A-H, rather
            # than posting a second historical instructed/imbalance assessment.
            account="A-H" if e.account in ("A-B","A-C") else e.account
            counterparty="A-H" if e.counterparty in ("A-B","A-C") else e.counterparty
            target.post(e.id,e.component,account,counterparty,e.quantity,e.price,e.period,e.rule,e.source,cash=e.cash_eur)
        return target
    old_related,new_related=related_accounts(original),related_accounts(recomputed)
    delta=engine.apply(revision_ledger,old_related,new_related,2,"meter_reconciliation",{"S24","S25","S26"})
    replay=engine.apply(revision_ledger,old_related,new_related,2,"meter_reconciliation",{"S24","S25","S26"})
    return revision_ledger,energy_adjustments,delta,replay

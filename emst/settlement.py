"""Energy classifications, source-labelled cash and an append-only Decimal ledger."""
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

D = lambda v: Decimal(str(v))
CENT = Decimal(".01")
CATALOGUE = {
    "S01":"Private forward", "S02":"DAM", "S03":"Instructed energy", "S04":"Uninstructed energy",
    "S05":"FCR", "S06":"aFRR", "S07":"mFRR", "S08":"RR", "S09":"Black start",
    "S10":"Emergency energy", "S11":"Contingency capacity", "S12":"Contingency energy",
    "S13":"Startup", "S14":"Transmission use", "S15":"Distribution use",
    "S16":"UA1", "S17":"UA2", "S18":"UA3", "S19":"UA4", "S20":"UA5",
    "S21":"UA6", "S22":"UA7", "S23":"Noncompliance", "S24":"Mandatory service",
    "S25":"Mandatory inherent energy", "S26":"Mandatory recovery", "S27":"Storage ancillary",
    "S28":"Storage recovery", "S29":"Monthly statement", "S30":"Cumulative settlement",
    "S31":"Meter reconciliation", "S32":"Invoices and payments", "S33":"Clearing and default"}


@dataclass(frozen=True)
class Posting:
    id: str
    component: str
    account: str
    counterparty: str
    cash_eur: str
    quantity: str
    price: str
    period: str
    rule: str
    source: str
    revision: int = 1
    previous: str | None = None
    source_formula_sign: str = "participant_receipt_positive"
    parameter_provenance: str = "synthetic_2026_11_v1"


class Ledger:
    def __init__(self):
        self.entries = []
        self._ids = {}

    def post(self, id, component, account, counterparty, quantity, price, period,
             rule, source, revision=1, previous=None, cash=None):
        if account == counterparty or component not in CATALOGUE:
            raise ValueError("Invalid account pair or component")
        q,p = D(quantity),D(price)
        amount = q*p if cash is None else D(cash)
        if not all(v.is_finite() for v in (q,p,amount)):
            raise ValueError("Nonfinite financial input")
        entry = Posting(id,component,account,counterparty,str(amount),str(q),str(p),
                        str(period),rule,source,revision,previous)
        if id in self._ids:
            if entry != self._ids[id]:
                raise ValueError(f"Conflicting immutable posting {id}")
            return Decimal(0)
        if previous and previous not in self._ids:
            raise ValueError("Revision has no original posting")
        self.entries.append(entry)
        self._ids[id] = entry
        return amount

    def revise(self, original_id, revision, new_total, source):
        original = self._ids[original_id]
        prior = D(original.cash_eur)+sum((D(e.cash_eur) for e in self.entries if e.previous==original_id),D(0))
        revision_id = f"{original_id}:revision:{revision}"
        if revision_id in self._ids:
            if self._ids[revision_id].source != source:
                raise ValueError("Conflicting revision source")
            return D(0)
        if revision <= max(e.revision for e in self.entries if e.id==original_id or e.previous==original_id):
            raise ValueError("Out-of-order revision")
        return self.post(revision_id,original.component,original.account,original.counterparty,
                         1,1,original.period,original.rule,source,revision,original_id,D(new_total)-prior)

    def balances(self, component=None):
        result = {}
        for e in self.entries:
            if component and e.component != component:
                continue
            amount = D(e.cash_eur)
            result[e.account] = result.get(e.account,D(0))+amount
            result[e.counterparty] = result.get(e.counterparty,D(0))-amount
        return result

    def export(self):
        return [asdict(e) for e in self.entries]


def production_bridge(msq, up, down, metered, tolerance, agc=False, startup=False):
    if min(msq,metered,up,down) < -1e-7 or not 0 <= tolerance < 1:
        raise ValueError("Production energy must use nonnegative role metering")
    instructed = msq+up-down
    if instructed < -1e-6:
        raise ValueError("Negative production instruction")
    if agc or startup:
        instq = metered-msq
        branch = "agc" if agc else "qualifying_startup"
    else:
        lower,upper = instructed*(1-tolerance),instructed*(1+tolerance)
        instq = min(max(metered,lower),upper)-msq
        branch = "ordinary_tolerance"
    return {"MSQ":msq,"INST":instructed,"MQ":metered,"INSTQ":instq,
            "UNINSTQ":metered-instq-msq,"branch":branch}


def res_bridge(msq, down, metered, settlement=None, grant_res=False):
    """RES instruction/meter bridge; policy tolerance is a fraction of instruction."""
    tolerances=(settlement or {}).get("res_tolerance", {"commercial":0.,"grant_supported":0.})
    tolerance=tolerances["grant_supported" if grant_res else "commercial"]
    return {**production_bridge(msq,0,down,metered,tolerance), "tolerance": tolerance}


def offtake_bridge(msq,metered,instq=0,tolerance=None):
    if min(msq,metered) < 0:
        raise ValueError("Use native positive offtake metering")
    adjusted = msq
    if tolerance is not None:
        if metered > msq*(1+tolerance):
            adjusted = msq*(1+tolerance)
        elif metered < msq*(1-tolerance):
            adjusted = msq*(1-tolerance)
    return {"MSQ":msq,"adjusted_MSQ":adjusted,"MQ":metered,"INSTQ":instq,
            "UNINSTQ":-(metered-instq-adjusted),
            "branch":"small_supplier" if tolerance is not None else "ordinary_offtake"}


def instructed_cash(instq, system, marginal_up, marginal_down, last_up, last_down):
    if system not in ("short","long"):
        raise ValueError("Zero-net RTUs require offer-specific settlement")
    if instq >= 0:
        return instq*(marginal_up if system=="short" else last_up)
    return -abs(instq)*(last_down if system=="short" else marginal_down)


def period_price(rtus, history):
    if len(history)!=3 or any(len(day)!=6 for day in history):
        raise ValueError("Three complete corresponding historical RTU price sets required")
    weighted = gross_weighted = weight_sum = gross_sum = 0.
    nets = []
    for r in rtus:
        eligible = [e for e in r["rows"]+r.get("res_offer_rows",[]) if e["price_forming"] and e["reason"] in ("ordinary_market","RES_down_offer")]
        net = sum(e["up_mwh"]-e["down_mwh"] for e in eligible)
        gross = sum(e["up_mwh"]+e["down_mwh"] for e in eligible)
        weight = abs(net)
        nets.append(net)
        weighted += weight*r["price_eur_mwh"]
        gross_weighted += gross*r["price_eur_mwh"]
        weight_sum += weight
        gross_sum += gross
    fallback = sum(float(p) for day in history for p in day)/18
    scarcity=[r.get('slack',{}).get('energy_shortage_mwh',0)+r.get('slack',{}).get('energy_surplus_mwh',0) for r in rtus]
    scarcity_fallback=weight_sum<=1e-7 and sum(scarcity)>1e-7
    marginal = weighted/weight_sum if weight_sum>1e-7 else (sum(w*r['price_eur_mwh'] for w,r in zip(scarcity,rtus))/sum(scarcity) if scarcity_fallback else fallback)
    sensitivity = gross_weighted/gross_sum if gross_sum>1e-7 else fallback
    return {"price_eur_mwh":marginal,"gross_sensitivity_eur_mwh":sensitivity,
            "net_weight_mwh":weight_sum,"gross_weight_mwh":gross_sum,
            "system":"short" if sum(nets)>=0 else "long",
            "historical_fallback":weight_sum<=1e-7 and not scarcity_fallback,"slack_penalty_fallback":scarcity_fallback,"rtu_net_mwh":nets,
            "conventions":["D02","D03"]}


def settle_energy(ledger, periods, rtus, registrations, schedules, history,brp_participants=None,settlement_rules=None):
    bridges,prices = [],[]
    for t in periods:
        batch = [r for r in rtus if r["period"]==t.period]
        price = period_price(batch,history[t.period])
        all_rows=[row for r in batch for row in r["rows"]+r.get("res_offer_rows",[])]
        last_up=max((r["up_offer_eur_mwh"] for r in all_rows if r["up_mwh"]>1e-7),default=price["price_eur_mwh"])
        last_down=min((r["down_offer_eur_mwh"] for r in all_rows if r["down_mwh"]>1e-7),default=price["price_eur_mwh"])
        prices.append({"period":t.period,**price})
        for e,reg in registrations.items():
            msq = schedules[e][t.period]
            account = f"participant:{reg.participant}:nonDAM"
            if reg.role=="production":
                rows = [row for r in batch for row in r["rows"] if row["entity"]==e]
                if not rows:  # RES is measured separately, explicitly instructed where curtailed.
                    actual = sum(r["entity_meters"][e] if e in r.get("entity_meters",{}) else r["res_actual_mwh"] for r in batch)
                    down = sum(r.get("entity_down",{}).get(e,r["res_curtail_mwh"] if e=="res" else 0) for r in batch)
                    bridge = res_bridge(msq,down,actual,settlement_rules,reg.grant_res)
                    market_offer=any(row['entity']==e for r in batch for row in r.get('res_offer_rows',[]))
                    if market_offer:
                        bridge.update(branch='RES_bid_activation')
                    controlled=any(i['entity']==e and i['external_control'] for r in batch for i in r.get('RES_instructions',[]))
                    if controlled:
                        inst=min(0.,actual-msq)
                        bridge.update(INSTQ=inst,UNINSTQ=actual-msq-inst,branch="PA_external_control")
                    inst_cash = 0. if reg.grant_res else instructed_cash(bridge["INSTQ"],price["system"],price["price_eur_mwh"],price["price_eur_mwh"],last_up,last_down)
                    offset_legs=[]
                    if market_offer and not reg.grant_res:
                        offset=offset_cash=0.
                        for k,rtu in enumerate(batch):
                            if abs(price['rtu_net_mwh'][k])<1e-7:
                                for row in rtu.get('res_offer_rows',[]):
                                    if row['entity']==e:
                                        delivered=rtu.get('entity_down',{}).get(e,row['down_mwh'])
                                        offset-=delivered;offset_cash-=delivered*row['down_offer_eur_mwh']
                                        offset_legs.append(dict(up_mwh=0,down_mwh=delivered,
                                            up_price=0,down_price=row['down_offer_eur_mwh']))
                        inst_cash=offset_cash+instructed_cash(bridge['INSTQ']-offset,price['system'],price['price_eur_mwh'],price['price_eur_mwh'],last_up,last_down)
                    bridge['payment_branch']='grant_RES_no_instructed_remuneration' if reg.grant_res else 'ordinary_nonthermal' 
                    bridge["accepted_net_mwh"]=-down
                    bridge["payment_inputs"]=dict(system=price["system"],marginal=price["price_eur_mwh"],
                        last_up=last_up,last_down=last_down,offset_legs=offset_legs)
                else:
                    actual = sum(r["actual_mwh"] for r in rows)
                    up = sum(r["up_mwh"] for r in rows)
                    down = sum(r["down_mwh"] for r in rows)
                    bridge = production_bridge(msq,up,down,actual,reg.tolerance,
                                               agc=all(r["agc"] for r in rows),
                                               startup=any(r.get("qualifying_startup",False) for r in rows))
                    if any(r.get("forced_unavailable",False) for r in rows):
                        net=up-down+sum(r["agc_energy_mwh"] for r in rows if not r.get("forced_unavailable",False))
                        bridge.update(INSTQ=net,UNINSTQ=actual-net-msq,branch="declared_forced_unavailability")
                    offset_signed = offset_cash = 0.;offset_legs=[]
                    for i,r in enumerate(batch):
                        if abs(price["rtu_net_mwh"][i]) < 1e-7:
                            for row in r["rows"]:
                                if row["entity"]==e:
                                    offset_signed += row["up_mwh"]-row["down_mwh"]
                                    offset_cash += row["up_mwh"]*row["up_offer_eur_mwh"]-row["down_mwh"]*row["down_offer_eur_mwh"]
                                    offset_legs.append(dict(up_mwh=row["up_mwh"],down_mwh=row["down_mwh"],
                                        up_price=row["up_offer_eur_mwh"],down_price=row["down_offer_eur_mwh"]))
                    ordinary = bridge["INSTQ"]-offset_signed
                    inst_cash = offset_cash+instructed_cash(ordinary,price["system"],price["price_eur_mwh"],price["price_eur_mwh"],last_up,last_down)
                    bridge.update(offset_offer_cash_eur=offset_cash,ordinary_instq_mwh=ordinary)
                    bridge["payment_inputs"]=dict(system=price["system"],marginal=price["price_eur_mwh"],
                        last_up=last_up,last_down=last_down,offset_legs=offset_legs)
                    bridge["accepted_net_mwh"]=up-down
                ledger.post(f"{e}:{t.id}:instructed","S03",account,"A-B",bridge["INSTQ"],
                            price["price_eur_mwh"],t.id,"D01/D05;13.4.6",f"RTU:{t.id}",cash=inst_cash)
            else:
                share = reg.consumption_share
                actual = sum(r["entity_meters"][e] if e in r.get("entity_meters",{}) else r["demand_actual_mwh"]*share for r in batch)
                bridge = offtake_bridge(msq,actual,tolerance=reg.tolerance if reg.small_supplier else None)
            uninst_cash = bridge["UNINSTQ"]*price["price_eur_mwh"]
            brp_owner=brp_participants[reg.brp] if brp_participants is not None else reg.participant
            ledger.post(f"{e}:{t.id}:uninstructed","S04",f"participant:{brp_owner}:nonDAM","A-B",bridge["UNINSTQ"],
                        price["price_eur_mwh"],t.id,"13.4.7-8/D05",f"meter:{reg.meter}:v1")
            bridges.append({"entity":e,"brp":reg.brp,"brp_participant":brp_owner,"period":t.period,**bridge,
                            "uninstructed_cash_eur":uninst_cash})
    return bridges,prices


def recover(ledger,component,source_account,amount,base,period,rule):
    total = sum(D(v) for v in base.values())
    if total<=0 and D(amount)!=0:
        raise ValueError("Nonzero recovery without an eligible allocation base")
    for participant,q in base.items():
        share = D(q)/total if total else D(0)
        ledger.post(f"{component}:{period}:{participant}",component,
                    f"participant:{participant}:nonDAM",source_account,
                    q, D(amount)/total if total else 0,period,rule,"eligible_consumption",cash=-D(amount)*share)


def reserve_cash(ledger,run_rows,availability,periods,units,emergency_periods=frozenset()):
    product_components = {"FCR_up":"S05","FCR_down":"S05","aFRR_up":"S06","aFRR_down":"S06",
                          "mFRR_up":"S07","mFRR_down":"S07","RR_up":"S08"}
    for row in run_rows:
        u = units[row["entity"]]
        for product,award in row["reserve_mw"].items():
            fraction = availability[u.id,row["period"],product]
            if not 0<=fraction<=1:
                raise ValueError("Invalid actual reserve availability fraction")
            q = award*fraction
            if product.startswith("mFRR") and row["period"] in emergency_periods:q=0
            rate = row["reserve_price_eur_mw_period"][product]
            ledger.post(f"reserve:{u.id}:{row['period']}:{product}",product_components[product],
                f"participant:{u.participant}:nonDAM","A-C",q,rate,periods[row["period"]].id,
                "13.5.2/R08/D04",f"effective_ISP:{row['period']}:{product}")


def assessed_service(ledger,event):
    """Explicit procurement/assessment inputs; absent fields never become zero."""
    required = {"id","component","participant","account","quantity","rate","period","rule","source","availability"}
    if not required <= event.keys():
        raise ValueError(f"Missing service inputs {required-event.keys()}")
    if not 0<=event["availability"]<=1:
        raise ValueError("Invalid service availability")
    return ledger.post(event["id"],event["component"],f"participant:{event['participant']}:nonDAM",
        event["account"],D(event["quantity"])*D(event["availability"]),event["rate"],event["period"],event["rule"],event["source"])


PENALTY_FAMILIES = (
    "invalid_DAM_offer","ancillary_non_delivery","invalid_physical_notification",
    "minimum_DAM_percentage","unavailability_declaration","technical_declaration",
    "missing_balancing_offer","missing_FCR_aFRR_mFRR_offer","missing_RR_contingency_offer",
    "dispatch_failure","significant_activation_failure","systematic_load_deviation",
    "systematic_RES_deviation","contracted_unit_failure","invalid_RR_notification")


def assess_penalty(event,configuration,counter_before):
    """Synthetic event-assessment engine, not a transcription of all 13.9 formulas.

    Configurations supply family-specific thresholds, rates, escalation and caps.
    Keep this qualification machine-readable; generic events alone do not certify
    monthly RMS tests or amended overlapping-category exclusions.
    """
    if event["family"] not in PENALTY_FAMILIES:
        raise ValueError("Unknown noncompliance family")
    required = {"threshold","rate","minimum","cap","escalation","source_clause"}
    if not required<=configuration.keys():
        raise ValueError("Incomplete noncompliance parameters")
    excluded = event.get("external_RES_control",False) and event["family"]=="systematic_RES_deviation"
    quantity = max(0,D(event["quantity"])-D(configuration["threshold"]))
    occurs = quantity>0 and not excluded
    count = counter_before+int(occurs)
    amount = D(0)
    if occurs:
        amount = min(D(configuration["cap"]),max(D(configuration["minimum"]),
            quantity*D(configuration["rate"])*(1+D(configuration["escalation"])*counter_before)))
    return {**event,"counter_before":counter_before,"counter_after":count,
            "amount_eur":str(amount),"exclusion_reason":"PA_external_control" if excluded else None,
            "source_clause":configuration["source_clause"],"parameter_provenance":"synthetic",
            "formula_status":"synthetic_event_assessment_not_full_13_9_transcription"}


def statement(ledger,scope,period,expected_days,received_days):
    if set(expected_days)!=set(received_days):
        raise ValueError("Statement history is incomplete")
    balances = ledger.balances()
    return {"scope":scope,"period":period,"status":"final_synthetic",
            "days":len(received_days),"balances_eur":{k:str(v) for k,v in balances.items()},
            "ledger_hash":hashlib.sha256(json.dumps(ledger.export(),sort_keys=True).encode()).hexdigest()}

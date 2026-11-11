"""Simulated invoices, collateral and ordered default waterfall; no bank I/O."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from .settlement import D, CENT
from .calendar import next_business_day
import hashlib,json


class ClearingCycle:
    """Validate, freeze and net a simulated financial-position file by account."""
    def __init__(self,cycle_id,active_members):
        self.id=cycle_id
        self.members=frozenset(active_members)
        self._snapshot=None

    def finalise(self,positions):
        ids=set();total=D(0);net={}
        for p in positions:
            if p["id"] in ids or p["member"] not in self.members:
                raise ValueError("Duplicate position/ineligible clearing member")
            ids.add(p["id"])
            if p["category"] not in ("DAM","MM","NON_DAM"):
                raise ValueError("Unknown clearing category")
            amount=D(p["value_eur"])
            if not amount.is_finite() or not p["source"] or not p["reference_period"]:
                raise ValueError("Invalid financial-position lineage")
            total+=amount
            key=(p["member"],p["clearing_account"],p["category"])
            net[key]=net.get(key,D(0))+amount
        if abs(total)>D(".000001"):
            raise ValueError("Unbalanced financial-position file")
        digest=hashlib.sha256(json.dumps(positions,sort_keys=True).encode()).hexdigest()
        if self._snapshot is not None and digest!=self._snapshot["hash"]:
            raise ValueError("Finalised clearing positions are immutable; submit an adjustment cycle")
        snapshot=dict(id=self.id,hash=digest,state="finalised",position_count=len(positions),
            balance_eur=str(total),net_positions=[dict(member=k[0],clearing_account=k[1],category=k[2],
            receipt_eur=str(v)) for k,v in sorted(net.items())])
        self._snapshot=snapshot
        # Copy the public value so a caller cannot mutate internal finalisation.
        return json.loads(json.dumps(snapshot))


def required_margin(unsettled_dam_mm_debits,last_mm_debit,six_month_obligations,minimum):
    if len(six_month_obligations)!=6:
        raise ValueError("Six previous monthly obligations required")
    if any(D(v)<0 for v in six_month_obligations) or D(minimum)<0:
        raise ValueError("Invalid exposure/minimum")
    current = max(D(0),D(unsettled_dam_mm_debits))
    future = 3*max(D(0),D(last_mm_debit))
    other = max(2*max(map(D,six_month_obligations)),D(minimum))
    return {"current_DAM_MM":str(current),"future_MM":str(future),
            "non_DAM_MM":str(other),"total":str(current+future+other),
            "unit":"EUR_collateral_not_expense","minimum_method":"synthetic_input"}


def invoice(id,lines,issued_on,tax_rate,holidays=frozenset(),revision=1,previous=None,cleared_on=None):
    if not lines:
        raise ValueError("Invoice needs assessed lines")
    if not 0<=D(tax_rate)<=1:
        raise ValueError("Tax rate must be a fraction between zero and one")
    net = sum(map(D,lines),D(0)).quantize(CENT,rounding=ROUND_HALF_UP)
    tax = (net*D(tax_rate)).quantize(CENT,rounding=ROUND_HALF_UP)
    return {"id":id,"revision":revision,"previous":previous,
            "document_type":"invoice" if net>=0 else "credit_note",
            "net_eur":str(net),"tax_eur":str(tax),"gross_eur":str(net+tax),
            "tax_rate":str(tax_rate),"tax_provenance":"synthetic_not_tax_advice",
            "issued_on":issued_on.isoformat(),"position_cleared_on":(cleared_on or issued_on).isoformat(),
            "due_on":next_business_day(cleared_on or issued_on,holidays).isoformat(),
            "state":"finalised","cash_deadline_local":"11:00","deadline_provenance":"synthetic"}


def pay(invoice_record,amount):
    if invoice_record["state"] not in ("finalised","partially_paid"):
        raise ValueError("Invoice is not payable")
    expected = abs(D(invoice_record["gross_eur"]))
    paid = D(invoice_record.get("paid_eur","0"))+D(amount)
    if D(amount)<0 or paid>expected:
        raise ValueError("Invalid payment")
    return {**invoice_record,"paid_eur":str(paid),
            "state":"paid" if paid==expected else "partially_paid"}


def default_waterfall(debt,collateral,own_fund,other_funds,reserve):
    values = [D(v) for v in (debt,collateral,own_fund,reserve)]
    if min(values)<0 or any(D(v)<0 for v in other_funds.values()):
        raise ValueError("Default quantities must be nonnegative")
    remaining = D(debt)
    draws = {}
    for key,balance in (("defaulter_collateral",D(collateral)),("defaulter_member_fund",D(own_fund))):
        draw = min(remaining,balance)
        draws[key] = str(draw)
        remaining -= draw
    pool = sum(map(D,other_funds.values()),D(0))
    used = min(remaining,pool)
    draws["other_members"] = {k:str(used*D(v)/pool if pool else D(0)) for k,v in other_funds.items()}
    remaining -= used
    reserve_draw = min(remaining,D(reserve))
    remaining -= reserve_draw
    draws["reserve_fund"] = str(reserve_draw)
    return {"debt_eur":str(debt),"draws_eur":draws,"uncovered_loss_eur":str(remaining),
            "reserve_remaining_eur":str(D(reserve)-reserve_draw),
            "defaulter_liability_eur":str(debt),
            "replenishment_required_eur":str(D(draws["defaulter_member_fund"])+used+reserve_draw)}


def late_payment_charge(debt,minutes_late):
    charge = max(D(500),D(debt)*D(".02")) if D(debt)>0 and minutes_late>=30 else D(0)
    return {"charge_eur":str(charge),"clearing_body_eur":str(min(charge,D(500))),
            "reallocation_eur":str(max(D(0),charge-500))}

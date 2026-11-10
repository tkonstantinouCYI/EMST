"""Financial state, role-specific minimum margin and simulated settlement calendars."""
from dataclasses import dataclass,field
from datetime import date,datetime,timedelta
from .settlement import D
from .clearing import default_waterfall,late_payment_charge


def minimum_margin(roles,wholesale_rate,unit_charges,res_fee,public_cap=None):
    """FR Appendix III; all six m-7..m-12 factors are supplied by role.

    The prescribed 732 hours and 119% coefficient are formula constants, not
    calibrated wholesale prices or today's VAT assumption.
    """
    amounts=[]
    for role in roles:
        factors=list(map(D,role["capacity_factors_m7_to_m12"]))
        if len(factors)!=6 or any(not 0<=f<=1 for f in factors):
            raise ValueError("Six historical role capacity factors required")
        q=D(732)*sum(factors,D(0))/3*D(role["capacity_mw_or_authorised_mva"])
        value=D(role["imbalance_factor"])*q*D(wholesale_rate)
        if role["role"]=="load_representative":
            value+=D(role["imbalance_factor"])*q*(D("1.19")*sum(map(D,unit_charges),D(0))+D(res_fee))
        if q<0 or value<0:raise ValueError("Invalid margin exposure")
        amounts.append({"role":role["role"],"estimated_two_month_mwh":str(q),"minimum_eur":str(value)})
    total=sum((D(a["minimum_eur"]) for a in amounts),D(0))
    if public_cap is not None:total=min(total,D(public_cap))
    return {"roles":amounts,"total_eur":str(total),"source":"FR Appendix III/R06 public-entity cap input"}


@dataclass
class Collateral:
    id: str
    account: str
    kind: str
    nominal_eur: str
    haircut: str
    expires_on: date | None
    used_eur: object=0

    def eligible(self,on):
        if self.kind not in ("cash","guarantee") or not 0<=D(self.haircut)<1:
            raise ValueError("Invalid collateral type/valuation")
        if self.expires_on and self.expires_on<on:return D(0)
        return max(D(0),D(self.nominal_eur)*(1-D(self.haircut))-D(self.used_eur))


@dataclass
class DefaultState:
    collateral: dict
    member_funds: dict
    reserve_eur: object
    liabilities: dict=field(default_factory=dict)
    failures: list=field(default_factory=list)
    processed: dict=field(default_factory=dict)

    def default(self,id,member,account,debt,on):
        if id in self.processed:
            record=self.processed[id]
            if (record["member"],record["account"],D(record["debt_eur"]))!=(member,account,D(debt)):
                raise ValueError("Conflicting default event")
            return record
        available=sum((c.eligible(on) for c in self.collateral.values() if c.account==account),D(0))
        own=D(self.member_funds[member])
        others={k:v for k,v in self.member_funds.items() if k!=member}
        result=default_waterfall(debt,available,own,others,self.reserve_eur)
        remaining=D(result["draws_eur"]["defaulter_collateral"])
        for c in self.collateral.values():
            if c.account!=account:continue
            take=min(remaining,c.eligible(on));c.used_eur=D(c.used_eur)+take;remaining-=take
        self.member_funds[member]=own-D(result["draws_eur"]["defaulter_member_fund"])
        for k,v in result["draws_eur"]["other_members"].items():self.member_funds[k]=D(self.member_funds[k])-D(v)
        self.reserve_eur=D(result["reserve_remaining_eur"])
        self.liabilities[member]=self.liabilities.get(member,D(0))+D(debt)
        record={**result,"id":id,"member":member,"account":account,"event_date":on.isoformat(),
                "restriction":"suspended_pending_cure","notifications":"simulation_records_only"}
        self.failures.append(record);self.processed[id]=record
        return record

    def replenish(self,member,amount):
        if D(amount)<0:raise ValueError("Negative replenishment")
        self.member_funds[member]=D(self.member_funds[member])+D(amount)


def overdue_invoice(invoice,deadline,as_of,annual_interest_rate,business_calendar):
    unpaid=abs(D(invoice["gross_eur"]))-D(invoice.get("paid_eur",0))
    minutes=max(0,(as_of-deadline).total_seconds()/60)
    late=late_payment_charge(unpaid,minutes)
    elapsed_days=D(str(max(0,(as_of-deadline).total_seconds())))/86400
    interest=unpaid*D(annual_interest_rate)*elapsed_days/365
    if deadline.date() not in business_calendar or as_of.date() not in business_calendar:
        raise ValueError("Explicit financial business-day table missing dates")
    return {**late,"unpaid_eur":str(unpaid),"interest_eur":str(interest),
            "interest_rate_provenance":"dated_synthetic_input","minutes_late":minutes}

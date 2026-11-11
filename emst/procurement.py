"""Replayable MM/MS procurement interfaces with source units and frozen shares."""
from dataclasses import dataclass
from decimal import ROUND_DOWN
import hashlib,json
from .settlement import D
from .accounting import allocate


@dataclass(frozen=True)
class FrozenShares:
    reference_month: str
    policy_version: str
    values: tuple

    @classmethod
    def from_absorption(cls,month,version,quantities):
        weights={k:D(v) for k,v in quantities.items()}
        if sum(weights.values(),D(0))<=0:raise ValueError("Missing positive MY reference-month history")
        shares=allocate(1,weights)
        return cls(month,version,tuple(sorted((k,str(v)) for k,v in shares.items())))

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps((self.reference_month,self.policy_version,self.values)).encode()).hexdigest()

    def inherent_energy(self,minimum_output_mw,hours):
        energy=sum(map(D,minimum_output_mw),D(0))*D(hours)
        return allocate(energy,dict(self.values))


def storage_auction(offers,required_mw,seed):
    from .operations import tied_selection
    weights={k:D(v) for k,v in offers.items()}
    if any(v<0 or v!=v.to_integral_value() for v in weights.values()):
        raise ValueError("MS offers must be integer MW quantities without prices")
    accepted=min(D(required_mw),sum(weights.values(),D(0)))
    raw=allocate(accepted,weights)
    awards={k:v.quantize(D(".01"),rounding=ROUND_DOWN) for k,v in raw.items()}
    cents=int(((accepted-sum(awards.values(),D(0)))/D(".01")).to_integral_value())
    order=tied_selection(weights.keys(),seed)
    order.sort(key=lambda k:raw[k]-awards[k],reverse=True)
    for k in order[:cents]:awards[k]+=D(".01")
    return {"awards_mw":{k:str(v) for k,v in awards.items()},
            "unprocured_mw":str(D(required_mw)-sum(awards.values(),D(0))),
            "quantity_rounding":"two_decimal_conserving_largest_remainder_seeded_ties",
            "source":"MS5.3.6-10"}


def storage_service_cash(actual_mw,mandatory_award_mw,optional_award_mw,fcr_price,coefficient):
    eligible=min(D(actual_mw),D(mandatory_award_mw)+D(optional_award_mw))
    if eligible<0:raise ValueError("Negative storage service delivery")
    return eligible*D(fcr_price)*D(coefficient)

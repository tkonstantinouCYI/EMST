"""Versioned two-sided forward notification and correction gates."""
from dataclasses import dataclass
from datetime import datetime
from .settlement import D


@dataclass(frozen=True)
class Notification:
    contract: str
    side: str
    participant: str
    quantities: tuple
    version: int
    submitted_at: datetime


class NotificationBook:
    def __init__(self,contracts,submission_gate,correction_gate):
        self.contracts={c.id:c for c in contracts}
        self.submission_gate=submission_gate;self.correction_gate=correction_gate
        self.records=[]

    def submit(self,n):
        c=self.contracts[n.contract]
        if n.side not in ("delivery","offtake"):raise ValueError("Unknown notification role")
        expected=c.seller if n.side=="delivery" else c.buyer
        if n.participant!=expected:raise ValueError("Wrong notification party")
        prior=[p for p in self.records if (p.contract,p.side)==(n.contract,n.side)]
        gate=self.correction_gate if prior else self.submission_gate
        if n.submitted_at>gate or n.version!=(max((p.version for p in prior),default=0)+1):
            raise ValueError("Notification version/deadline violation")
        if len(n.quantities)!=len(c.energy_mwh) or any(D(q)<0 for q in n.quantities):
            raise ValueError("Invalid notification horizon/quantity")
        self.records.append(n)

    def finalise(self):
        accepted=[];mismatches=[]
        for id,c in self.contracts.items():
            legs={}
            for side in ("delivery","offtake"):
                choices=[n for n in self.records if n.contract==id and n.side==side]
                if not choices:raise ValueError(f"Missing {side} notification {id}")
                legs[side]=max(choices,key=lambda n:n.version)
            mismatch=[abs(D(a)-D(b)) for a,b in zip(legs["delivery"].quantities,legs["offtake"].quantities)]
            if any(mismatch):mismatches.append({"contract":id,"mismatch_mwh":list(map(str,mismatch))})
            elif tuple(map(D,legs["delivery"].quantities))!=tuple(map(D,c.energy_mwh)):
                raise ValueError("Matched notifications disagree with contract physical allocation")
            else:accepted.append(c)
        return accepted,mismatches

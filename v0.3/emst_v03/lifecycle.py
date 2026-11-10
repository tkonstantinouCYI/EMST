"""Immutable statement versions, objections, corrections and simulated cash books."""
from dataclasses import dataclass,field
from datetime import datetime
import hashlib,json
from .settlement import D
from .clearing import invoice


class StatementBook:
    def __init__(self):self.versions={};self.objections={}

    def preliminary(self,id,lines,issued_at,objection_deadline):
        if id in self.versions:raise ValueError("Statement already issued")
        if issued_at.tzinfo is None or objection_deadline<issued_at:raise ValueError("Invalid statement dates")
        data=dict(id=id,version=1,state="preliminary",lines=lines,issued_at=issued_at.isoformat(),
                  objection_deadline=objection_deadline.isoformat())
        self.versions[id]=[json.loads(json.dumps(data,default=str))]
        return json.loads(json.dumps(self.versions[id][-1]))

    def object(self,id,participant,reason,at):
        current=self.versions[id][-1]
        if (at.tzinfo is None or current["state"]!="preliminary" or
            not datetime.fromisoformat(current["issued_at"])<=at<=datetime.fromisoformat(current["objection_deadline"])):
            raise ValueError("Statement objection window closed")
        self.objections.setdefault(id,[]).append(dict(participant=participant,reason=reason,at=at.isoformat()))

    def finalise(self,id,lines,at,decisions):
        current=self.versions[id][-1]
        if current["state"]=="final":raise ValueError("Final statement cannot be overwritten")
        if at.tzinfo is None or at<datetime.fromisoformat(current["objection_deadline"]):
            raise ValueError("Final statement precedes objection deadline")
        if len(decisions)!=len(self.objections.get(id,[])):raise ValueError("Every objection requires a decision")
        final=dict(id=id,version=current["version"]+1,state="final",lines=lines,issued_at=at.isoformat(),
                   previous_version=current["version"],objection_decisions=decisions,
                   content_hash=hashlib.sha256(json.dumps(lines,sort_keys=True,default=str).encode()).hexdigest())
        self.versions[id].append(final)
        return json.loads(json.dumps(final,default=str))


def invoice_correction(original,new_net,issued_on,tax_rate,holidays):
    delta=D(new_net)-D(original["net_eur"])
    return invoice(f"{original['id']}:adjustment:{original['revision']+1}",[delta],issued_on,tax_rate,
                   holidays,revision=original["revision"]+1,previous=original["id"])


@dataclass
class CashBook:
    balances: dict
    restricted: dict=field(default_factory=dict)
    transfers: list=field(default_factory=list)
    ids: dict=field(default_factory=dict)

    def transfer(self,id,sender,receiver,amount,at,source):
        q=D(amount)
        if q<0 or sender==receiver or not source or at.tzinfo is None:raise ValueError("Invalid cash transfer")
        if id in self.ids:
            if self.ids[id]!=(sender,receiver,q):raise ValueError("Conflicting cash transfer")
            return D(0)
        if sender not in self.balances or receiver not in self.balances:raise ValueError("Missing opening cash")
        available=D(self.balances[sender])-D(self.restricted.get(sender,0))
        if available<q:raise ValueError("Insufficient unrestricted cash")
        self.balances[sender]=D(self.balances[sender])-q
        self.balances[receiver]=D(self.balances[receiver])+q
        self.ids[id]=(sender,receiver,q)
        self.transfers.append(dict(id=id,sender=sender,receiver=receiver,amount_eur=str(q),at=at.isoformat(),source=source))
        return q

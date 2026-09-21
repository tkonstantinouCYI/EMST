"""Case-study allocation policy using the library's public solver interface.

Preserves the CBC block selection, primary bid surplus and prices. With those
fixed, optionally completes equal-price buy/sell trades and allocates each
(period, side, exact price) group's accepted quantity pro rata to offered energy.
This is not a general tie rule for linked or fractional block orders.
"""
from collections import defaultdict
from emst.solvers import PulpMarketClearingSolver

class CaseStudySolver(PulpMarketClearingSolver):
    def __init__(self, policy="max_volume_pro_rata"):
        super().__init__()
        if policy not in ("cbc", "pro_rata", "max_volume_pro_rata"):
            raise ValueError(policy)
        self.policy=policy

    def clear(self, **kwargs):
        # Canonical order removes input-list order as a solver implementation detail.
        kwargs=dict(kwargs)
        bids=sorted(kwargs["bids"], key=lambda b:b.bid_id)
        kwargs["bids"]=bids
        blocks=sorted(kwargs.get("block_bids") or [], key=lambda b:b.bid_id)
        kwargs["block_bids"]=blocks
        if self.policy != "cbc" and (kwargs.get("circular_block_bids") or any(not b.acceptance_binary or b.parent_bid_id for b in blocks)):
            raise ValueError("Case-study tie policy supports simple bids and independent binary blocks only")
        result=super().clear(**kwargs)
        if self.policy=="cbc": return result
        accepted=result["accepted_bids"]
        groups=defaultdict(list)
        for b in bids:
            if b.quantity>0: groups[b.period,b.side,b.price].append(b)
        totals={k:sum(accepted.get(b.bid_id,0) for b in group) for k,group in groups.items()}
        capacities={k:sum(b.quantity for b in group) for k,group in groups.items()}
        old_volume={p:sum(accepted.get(b.bid_id,0) for b in bids if b.period==p and b.side=="buy") for p in kwargs["periods"]}
        added=0.0
        if self.policy=="max_volume_pro_rata":
            for period,price in sorted({(b.period,b.price) for b in bids}):
                buy=(period,"buy",price);sell=(period,"sell",price)
                if buy not in groups or sell not in groups:continue
                extra=max(0.0,min(capacities[buy]-totals[buy],capacities[sell]-totals[sell]))
                totals[buy]+=extra;totals[sell]+=extra;added+=extra
                if extra>1e-9 and old_volume[period]<=1e-9:
                    result["prices"][period]=round(price,6)
        old_w=sum((1 if b.side=="buy" else -1)*b.price*accepted.get(b.bid_id,0) for b in bids)
        for key,group in groups.items():
            for b in group:
                accepted[b.bid_id]=totals[key]*b.quantity/capacities[key]
                assert -1e-5<=accepted[b.bid_id]<=b.quantity+1e-5
        new_w=sum((1 if b.side=="buy" else -1)*b.price*accepted.get(b.bid_id,0) for b in bids)
        assert abs(new_w-old_w)<1e-5,(new_w,old_w)
        positions=defaultdict(lambda:defaultdict(float))
        for b in bids:
            positions[b.participant_id][b.period]+=(1 if b.side=="sell" else -1)*accepted.get(b.bid_id,0)
        for b in blocks:
            for period,q in zip(b.periods,b.quantities):
                positions[b.participant_id][period]+=(1 if b.side=="sell" else -1)*q*accepted.get(b.bid_id,0)
        for period in kwargs["periods"]:
            assert abs(sum(v.get(period,0) for v in positions.values()))<1e-4
        result["participant_positions"]={k:dict(v) for k,v in positions.items()}
        result["metadata"].update(tie_policy=self.policy,extra_zero_surplus_mwh=added,
            block_acceptances={b.bid_id:accepted.get(b.bid_id,0) for b in blocks})
        result["metadata"]["simple_orders"]=[dict(participant=b.participant_id,period=b.period,side=b.side,quantity=b.quantity,price=b.price,accepted=accepted.get(b.bid_id,0)) for b in bids]
        return result

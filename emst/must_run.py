"""Simplified MW-based, pay-as-bid must-run procurement (not inertia procurement)."""
from copy import deepcopy
import math
import pulp as lp
from .optimization import solve
from .series import mw_values
from .settlement import D


def clear_auction(market, requirements, *, frequency='daily', auction_id=None):
    """Clear all supplied dates in one auction, with independent unit/MTU awards.

    Offers are EUR/unit/MTU; awarded contribution is minimum stable MW, never
    nameplate capacity. Technical feasibility is subsequently enforced by ISP.
    """
    units=[a for a in market.assets.values() if a.technology=='thermal'
           and a.parameters.get('must_run_participation',False)]
    values={day:mw_values(v,market.time_index(day),'must-run requirement MW')
            for day,v in requirements.items()}
    total=sum(a.parameters['minimum_mw'] for a in units)
    if any(max(v,default=0)>total+1e-7 for v in values.values()):
        raise ValueError('Must-run requirement exceeds eligible minimum stable MW')
    model=lp.LpProblem('must_run_auction',lp.LpMinimize)
    awards={};terms=[]
    for day,series in values.items():
        for t,requirement in enumerate(series):
            for i,a in enumerate(units):
                x=lp.LpVariable(f'award_{day}_{t}_{i}',cat='Binary')
                awards[day,t,a.id]=x
                if requirement==0:model+=x==0
                terms.append(x*a.parameters['must_run_offer_eur_per_mtu'])
            if units:model+=lp.lpSum(a.parameters['minimum_mw']*awards[day,t,a.id] for a in units)>=requirement
    model+=lp.lpSum(terms)
    objective=solve(model) if awards else 0.
    auction_id=auction_id or f'{frequency}:{min(values)}:{max(values)}'
    result={}
    for day,series in values.items():
        rows=[]
        for t,requirement in enumerate(series):
            selected=[dict(asset_id=a.id,participant=a.participant,
                minimum_mw=a.parameters['minimum_mw'],energy_mwh=a.parameters['minimum_mw']*.5,
                offer_eur_per_mtu=a.parameters['must_run_offer_eur_per_mtu'])
                for a in units if awards[day,t,a.id].value()>.5]
            rows.append(dict(period_index=t,requirement_mw=requirement,
                awarded_mw=sum(a['minimum_mw'] for a in selected),awards=selected,
                service_cost_eur=sum(a['offer_eur_per_mtu'] for a in selected)))
        result[day]=dict(auction_id=auction_id,frequency=frequency,
            model='minimum_stable_generation_MW',auction_objective_eur=objective,intervals=rows)
    return result


def supplier_weights(market, forecasts, supplied=None):
    suppliers=[a for a in market.assets.values() if a.technology=='supplier']
    owners={a.participant for a in suppliers}
    weights={p:sum(sum(forecasts[a.id]) for a in suppliers if a.participant==p) for p in sorted(owners)}
    if supplied is not None:
        if set(supplied)!=owners:raise ValueError('Must-run supplier shares must cover supplier participant IDs exactly')
        weights={p:float(v) for p,v in supplied.items()}
        if any(not math.isfinite(v) or v<0 for v in weights.values()) or abs(sum(weights.values())-1)>1e-8:
            raise ValueError('Must-run supplier shares must be finite, nonnegative and sum to one')
    if sum(weights.values())<=0:raise ValueError('Must-run allocation requires positive supplier demand shares')
    total=sum(weights.values())
    return {p:v/total for p,v in weights.items()}


def allocate_awards(market, award, forecast, shares=None):
    award=deepcopy(award)
    weights=supplier_weights(market,forecast,shares)
    suppliers=[a for a in market.assets.values() if a.technology=='supplier']
    energy={a.id:[0.]*48 for a in market.assets.values()}
    for row in award['intervals']:
        t=row['period_index'];q=row['awarded_mw']*.5
        row['supplier_allocations']=[]
        for a in row['awards']:energy[a['asset_id']][t]=a['energy_mwh']
        for owner,share in sorted(weights.items()):
            portfolios=[a for a in suppliers if a.participant==owner]
            # Split a participant's fixed share across its load portfolios using
            # their same-MTU forecasts. No actual meter data enters procurement.
            denominator=sum(forecast[a.id][t] for a in portfolios)
            for a in portfolios:
                fraction=forecast[a.id][t]/denominator if denominator else 1/len(portfolios)
                value=q*share*fraction
                energy[a.id][t]=value
                row['supplier_allocations'].append(dict(asset_id=a.id,participant=owner,energy_mwh=value))
    award['supplier_shares']=weights
    award['supplier_share_source']=award.get('supplier_share_source','supplied' if shares is not None else 'forecast_consumption')
    return award,energy


def post_settlement(ledger, award, periods):
    """Keep service recovery outside UA1/UA2 and inherent energy cash at zero."""
    for row in award['intervals']:
        t=row['period_index'];period=periods[t].id;cost=D(0)
        for a in row['awards']:
            price=D(a['offer_eur_per_mtu']);cost+=price
            ledger.post(f'must_run:service:{a["asset_id"]}:{t}','S24',
                f'participant:{a["participant"]}:must_run','MUST-RUN',1,price,period,
                'simplified_MW_must_run','pay_as_bid_award')
            ledger.post(f'must_run:energy:{a["asset_id"]}:{t}','S25',
                f'participant:{a["participant"]}:must_run','MUST-RUN',a['energy_mwh'],0,period,
                'simplified_MW_must_run','zero_price_inherent_energy')
        for a in row['supplier_allocations']:
            ledger.post(f'must_run:energy:{a["asset_id"]}:{t}','S25',
                f'participant:{a["participant"]}:must_run','MUST-RUN',-a['energy_mwh'],0,period,
                'simplified_MW_must_run','zero_price_inherent_energy')
        owners=sorted(award['supplier_shares']);remaining=cost
        for i,owner in enumerate(owners):
            charge=remaining if i==len(owners)-1 else cost*D(award['supplier_shares'][owner])
            remaining-=charge
            ledger.post(f'must_run:recovery:{owner}:{t}','S26',
                f'participant:{owner}:must_run','MUST-RUN',1,1,period,
                'simplified_MW_must_run','fixed_supplier_shares',cash=-charge)

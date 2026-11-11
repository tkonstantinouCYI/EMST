"""Explicit high-RES control, trip transitions and exceptional run horizons."""
import random
import math
from copy import deepcopy
from .settlement import D


def project_handover(units,intervals,state,isp_rows,schedules,demand,res,storage_discharge,history,cutoff,effective_at,seed,previous_observation=None,system_forecasts_rtu=None,storage_charge=None,res_offer_price=None,res_participant=None,slack_parameters=None,requirements=None):
    """Forecast the activation-boundary state using only cutoff-known inputs.

    Existing ISP awards remain binding during this forecast bridge. Forecast
    delivery equals forecast instructions; future telemetry/failures are not inputs.
    The original physical state is never advanced by this planning calculation.
    """
    from .rtbm import clear_rtu
    from .system import observation_update
    if not intervals or intervals[0].start!=cutoff or intervals[-1].end!=effective_at:
        raise ValueError("Complete cutoff-to-effective-time forecast bridge required")
    if any(a.end!=b.start for a,b in zip(intervals,intervals[1:])):
        raise ValueError("Forecast bridge gap or overlap")
    projected=deepcopy(state);trace=[]
    for interval in intervals:
        p=interval.period
        i=p*6+(interval.local.minute%30)//5
        baseline_d=demand[p];baseline_r=res[p]
        if system_forecasts_rtu is not None:
            baseline_d=system_forecasts_rtu['demand'][i]+storage_charge[p]/.5
            baseline_r=system_forecasts_rtu['res'][i]+storage_discharge[p]/.5
        prediction=observation_update(baseline_d,baseline_r,previous=previous_observation)
        d=prediction['demand_prediction_mw'];r=prediction['res_prediction_mw']
        result=clear_rtu(interval,units,projected,{u.id:isp_rows[u.id,p] for u in units},
            {u.id:schedules[u.id][p]/6 for u in units},d,r,d,r,
            delivery_equals_dispatch=True,slack_parameters=slack_parameters,
            res_energy_offers=None if res_offer_price is None else [dict(id='res',participant=res_participant,
                available_mwh=max(0,r*interval.hours-storage_discharge[p]/6),commercial_mwh=schedules['res'][p]/6,down_price=res_offer_price)],
            high_res_reference_price=sum(day[i%6] for day in history[p])/3,
            res_portfolios=[dict(id="res",category=3,available_mwh=max(0,r*interval.hours-storage_discharge[p]/6),
                technical_minimum_mwh=0,zero_injection_self_consumption=False)],seed=seed)
        trace.append(dict(rtu=interval.id,start=interval.start.isoformat(),end=interval.end.isoformat(),
            forecast_demand_mw=d,forecast_res_mw=r,state=deepcopy(projected)))
    return dict(cutoff=cutoff.isoformat(),effective_at=effective_at.isoformat(),
        observed_state=deepcopy(state),projected_state=projected,trace=trace,
        method="forecast_RTBM_under_existing_ISP_awards_no_future_actuals",
        previous_observation=deepcopy(previous_observation))


def priority_curtailment(portfolios,required_mwh,seed):
    """PA tools 1 then 2: proportional category 3, then priority category 2.

    Category 1 cannot be curtailed by this mechanism. Zero-injection self-consumed
    energy is outside the pool. Ordinary equal-price offer selection is seeded
    separately and does not replace the PA proportional rule.
    """
    remaining=D(required_mwh)
    if remaining<0:raise ValueError("Negative curtailment")
    instructions=[]
    for category in (3,2):
        eligible=[p for p in portfolios if p["category"]==category and not p["zero_injection_self_consumption"]]
        available=sum((D(p["available_mwh"])-D(p["technical_minimum_mwh"]) for p in eligible),D(0))
        take=min(remaining,available)
        if take<0:raise ValueError("Invalid RES technical minimum")
        for p in sorted(eligible,key=lambda p:p["id"]):
            q=take*(D(p["available_mwh"])-D(p["technical_minimum_mwh"]))/available if available else D(0)
            instructions.append(dict(entity=p["id"],down_mwh=str(q),price_forming=False,
                reason=f"PA_tool_{1 if category==3 else 2}",external_control=True,category=category,
                systematic_penalty_exempt=True))
        remaining-=take
    if remaining>D("1e-9"):
        raise RuntimeError("PA controllable RES exhausted; explicit emergency action required")
    return instructions


def tied_selection(offer_ids,seed):
    ids=sorted(offer_ids);random.Random(seed).shuffle(ids)
    return ids


def trip(state,entity,effective_at):
    prior=dict(state[entity])
    state[entity]={"on":0,"end_mw":0.,"duration_rtus":0,"forced_trip_at":effective_at.isoformat()}
    return {"entity":entity,"effective_at":effective_at.isoformat(),"prior_state":prior,
            "actual_state":dict(state[entity]),"cause":"forced_trip",
            "ramp_exemption":"involuntary_trip_only","requires_exceptional_ISP":True}


def rerun_horizon(periods,effective_at):
    # An arbitrary-time event replaces only the next full trading period; RTBM
    # retains immediate outage handling while that schedule becomes effective.
    remaining=[p for p in periods if p.start>=effective_at]
    if not remaining:raise ValueError("No future-effective ISP horizon remains")
    return remaining


def emergency_energy(registered_capacity_mw,measured_mwh,instructed_mwh,hours,cost_eur_mwh):
    """13.5.2.4: compensate qualified delivered above-capacity energy only."""
    threshold=D(registered_capacity_mw)*D(hours)
    eligible=max(D(0),min(D(measured_mwh),D(instructed_mwh))-threshold)
    return {"eligible_mwh":str(eligible),"cash_eur":str(eligible*D(cost_eur_mwh)),
            "source_clause":"13.5.2.4","ordinary_balancing_payment_excluded":True}


def contingency_energy(instructed_mwh,delivered_mwh,contract_rate):
    eligible=min(D(instructed_mwh),D(delivered_mwh))
    if eligible<0:raise ValueError("Negative contingency delivery")
    return {"eligible_mwh":str(eligible),"cash_eur":str(eligible*D(contract_rate)),
            "source_clause":"13.4.6.11","ordinary_balancing_payment_excluded":True}

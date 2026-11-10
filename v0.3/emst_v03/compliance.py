"""Chapter 13.9 assessment, amended exclusions and persistent occurrence counters.

Rates/exponents are dated external inputs. Formula IDs refer to the frozen source
archive; this module never treats a numerical demonstration rate as approved.
"""
from dataclasses import dataclass,field
from .settlement import D
from decimal import ROUND_HALF_UP

CAPACITY = {"invalid_DAM_offer":"9.2", "unavailability_declaration":"9.6",
            "technical_declaration":"9.7", "missing_balancing_offer":"9.8",
            "missing_FCR_aFRR_mFRR_offer":"9.9", "missing_RR_contingency_offer":"9.10.1",
            "invalid_RR_notification":"9.16"}


def daily_activation_penalties(ledger,bridges,registrations,day,parameters,opening_counters):
    """Assess day-observable activation failures; monthly statistics stay deferred."""
    year=str(day.year)
    counters=Counters({(e,"significant_activation_failure",year):n for e,n in opening_counters.items()})
    results=[]
    for b in bridges:
        entity=b["entity"]
        if entity not in opening_counters:continue
        net=D(b.get("accepted_net_mwh",0));instruction=D(b["MSQ"])+net;meter=D(b["MQ"])
        tol=D(parameters["tolerance"])
        excess=max(D(0),meter-instruction*(1+tol),instruction*(1-tol)-meter)
        event=dict(entity=entity,family="significant_activation_failure",obligated=True,role="thermal",
            failure=bool(net and excess>D("1e-9")),counter_window=year,occurrence_key=day.isoformat(),
            schedule_mwh=b["MSQ"],up_mwh=max(D(0),net),down_mwh=max(D(0),-net),meter_mwh=meter,
            event_time=day.isoformat()+"T00:00:00+02:00",assessment_period=str(b["period"]))
        result=assess(event,parameters,counters);results.append(result)
        ledger.post(f"daily_activation_failure:{entity}:{b['period']}","S23",
            f"participant:{registrations[entity].participant}:nonDAM","A-E",-D(result["amount_eur"]),1,
            f"{day}:{b['period']}",result["source_clause"],"daily_instruction_and_meter")
    return results


@dataclass
class Counters:
    opening: dict
    seen: dict=field(default_factory=dict)

    def occurrence(self,entity,family,window,key,active):
        identity=(entity,family,window)
        if identity not in self.opening:
            raise ValueError(f"Missing opening occurrence history: {identity}")
        events=self.seen.setdefault(identity,set())
        before=int(self.opening[identity])+len(events)
        if active:events.add(key)
        return before,int(self.opening[identity])+len(events)


def systematic(records,expected_periods,role,rates):
    """13.9.13/14: root-sum-square, not an invented mean-normalised RMS.

    R08 uses the symmetric schedule/meter denominator. PA-controlled RES periods
    are omitted from both numerator and denominator. Zero deviations return zero;
    a nonzero deviation with a zero/negative normalisation base is an input error.
    """
    if {r["period"] for r in records}!=set(expected_periods) or len(records)!=len(expected_periods):
        raise ValueError("Complete unique assessment-period meter history required")
    deviations=[];bases=[]
    for r in records:
        if role=="RES" and r.get("external_control",False):continue
        schedule,meter=D(r["schedule_mwh"]),D(r["meter_mwh"])
        down=D(r.get("down_mwh",0))
        if min(schedule,meter,down)<0:raise ValueError("Invalid native energy history")
        if role=="RES":
            deviation=schedule-down-meter
            base=abs((schedule+meter)/2)-down
        elif role=="load":
            deviation=meter-D(r["adjusted_schedule_mwh"])
            base=(schedule+meter)/2
        else:raise ValueError("Unknown systematic-deviation role")
        deviations.append(deviation);bases.append(base)
    adev=sum(map(abs,deviations),D(0))
    rms=sum((v*v for v in deviations),D(0)).sqrt()
    absolute_base=sum(bases,D(0))
    rms_base=sum((v*v for v in bases),D(0)).sqrt()
    if adev and (absolute_base<=0 or rms_base<=0):
        raise ValueError("Nonzero deviation has invalid normalisation denominator")
    na=adev/absolute_base if adev else D(0)
    nr=rms/rms_base if rms else D(0)
    cash=max(D(0),D(rates["absolute_rate"])*adev*(na-D(rates["absolute_tolerance"])),
             D(rates["rms_rate"])*rms*(nr-D(rates["rms_tolerance"])))
    return {"ADEV":str(adev),"RMSDEV":str(rms),"NADEV":str(na),"NRMSDEV":str(nr),
            "absolute_denominator":str(absolute_base),"rms_denominator":str(rms_base),
            "amount_eur":str(cash),"eligible_periods":len(deviations),
            "formula_id":"13.9.14/R08/PA" if role=="RES" else "13.9.13/R08"}


def assess(event,parameters,counters):
    family=event["family"]
    # Eligibility is evaluated before touching counters or requiring irrelevant rates.
    exclusion=None
    if not event["obligated"]:exclusion="no_applicable_obligation"
    if event.get("mandatory_framework",False):exclusion="MM_separate_penalty_framework"
    if family=="minimum_DAM_percentage" and not event.get("dominant_supplier",False):
        exclusion="not_dominant_supplier"
    if family=="dispatch_failure" and event.get("absolute_power_deviations_mw"):
        series=list(map(D,event["absolute_power_deviations_mw"]))
        if sum(series,D(0))/len(series)<=D(".5"):exclusion="monthly_mean_not_above_0.5_MW"
    if family=="significant_activation_failure" and event.get("role")=="RES":
        exclusion="R08_removes_RES_from_13.9.12"
    if family=="systematic_RES_deviation" and event.get("external_control",False):
        exclusion="PA_external_control"
    active=bool(event["failure"] and exclusion is None)
    before,count=counters.occurrence(event["entity"],family,event["counter_window"],event["occurrence_key"],active)
    clauses=CAPACITY|{"ancillary_non_delivery":"9.3","invalid_physical_notification":"9.4",
        "minimum_DAM_percentage":"9.5","missing_black_start_offer":"9.10.2","dispatch_failure":"9.11",
        "significant_activation_failure":"9.12/R08","systematic_load_deviation":"9.13/R08",
        "systematic_RES_deviation":"9.14/R08/PA","contracted_unit_failure":"9.15"}
    if family not in clauses:raise ValueError(f"Unknown penalty family {family}")
    amount=D(0);formula="13."+clauses[family]
    if active:
        if family in CAPACITY:
            exponent=D(parameters["exponent"])
            if not 0<=exponent<=1:raise ValueError("Counter exponent must lie in [0,1]")
            quantities=list(map(D,event["missing_quantities"]))
            if min(quantities,default=D(0))<0:raise ValueError("Negative missing capability")
            amount=D(parameters["rate"])*(1+D(parameters["escalation"]))*D(count)**exponent*sum(quantities,D(0))
        elif family=="ancillary_non_delivery":
            amount=(1+D(parameters["escalation"]))*D(count)**D(parameters["exponent"])*(
                D(event["missing_mFRR_fraction"])*D(event["mFRR_remuneration_eur"])+
                D(event["missing_RR_fraction"])*D(event["RR_remuneration_eur"]))
            formula="13.9.3"
        elif family=="invalid_physical_notification":
            amount=D(event["mismatch_mwh"])*D(event["dam_price_eur_mwh"])*D(parameters["mismatch_coefficient"])
            formula="13.9.4"
        elif family=="minimum_DAM_percentage":
            if not event["dominant_supplier"]:exclusion="not_dominant_supplier";amount=D(0)
            else:
                y=D(event["dam_buy_mwh"]);ndp=D(event["net_delivery_mwh"])
                amount=max(D(0),ndp-(ndp+y)*(1-D(parameters["minimum_fraction"]))) * D(parameters["dam_cap"])
            formula="13.9.5"
        elif family=="missing_black_start_offer":
            amount=D(500000)/12;formula="13.9.10.2"
        elif family=="dispatch_failure":
            deviations=list(map(D,event["absolute_power_deviations_mw"]))
            if not deviations:raise ValueError("Missing complete instruction deviation series")
            mean=sum(deviations,D(0))/len(deviations)
            amount=D(parameters["rate"])*(1+D(parameters["escalation"]))*count*mean if mean>D(".5") else D(0)
            formula="13.9.11"
        elif family=="significant_activation_failure":
            s,u,d,m=map(D,(event["schedule_mwh"],event["up_mwh"],event["down_mwh"],event["meter_mwh"]))
            instruction=s+u-d
            excess=max(D(0),m-instruction*(1+D(parameters["tolerance"])),instruction*(1-D(parameters["tolerance"]))-m)
            amount=D(parameters["rate"])*(1+D(parameters["escalation"]))*excess if u or d else D(0)
            formula="13.9.12/R08"
        elif family in ("systematic_load_deviation","systematic_RES_deviation"):
            stats=systematic(event["records"],event["expected_periods"],
                             "RES" if family=="systematic_RES_deviation" else "load",parameters)
            amount=D(stats["amount_eur"]);formula=stats["formula_id"]
        elif family=="contracted_unit_failure":
            # The second occurrence is on a different calendar day; further
            # sanctions are authority decisions, never fabricated cash charges.
            factor=2*D(event["failed_settlement_periods"]) if count==1 else D(8760) if count==2 else D(0)
            amount=factor*D(event["registered_capacity_mw"])*D(event["contingency_price"])
            formula="13.9.15"
        else:raise ValueError(f"Unknown penalty family {family}")
    return {**event,"counter_before":before,"counter_after":count,"amount_eur":str(amount),
            "source_clause":formula,"source_version":"R00+R04+R07+R08; frozen_2026_11_05",
            "exclusion_reason":exclusion,"parameter_provenance":parameters["provenance"],
            "assessment_status":"excluded" if exclusion else "assessed"}


def mandatory_penalty(kind,occurrences,quantity,rate,escalation,counter):
    """MM 4.5: counts NOP/NOS, unavailable hours or frozen supplier share."""
    if kind not in ("producer","supplier"):raise ValueError("Unknown MM penalty")
    return D(occurrences)*D(quantity)*D(rate)*(1+D(escalation))**int(counter)


def blackstart_instalment(month):
    if not 1<=month<=12:raise ValueError("Invalid instalment month")
    ordinary=(D(500000)/12).quantize(D(".01"),rounding=ROUND_HALF_UP)
    return ordinary if month<12 else D(500000)-11*ordinary


@dataclass(frozen=True)
class ParameterBook:
    records: tuple

    def at(self,family,when):
        candidates=[r for r in self.records if r["family"]==family and r["valid_from"]<=when<r["valid_to"]]
        if len(candidates)!=1:raise ValueError("Missing or overlapping dated penalty parameter sets")
        record=candidates[0]
        if not record["approval_reference"] or not record["provenance"]:
            raise ValueError("Parameter approval/provenance is required, including synthetic IDs")
        return record["values"]|{"provenance":record["provenance"]}

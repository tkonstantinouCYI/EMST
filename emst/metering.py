"""Role-separated meters, explicit loss factors, profiles and storage accounting."""
from dataclasses import dataclass
from datetime import datetime
from .settlement import D


@dataclass(frozen=True)
class Meter:
    id: str
    entity: str
    period: str
    quantity_mwh: str
    loss_multiplier: str
    role: str
    status: str
    revision: int
    acquired_at: datetime

    def adjusted(self):
        if self.role not in ("injection","offtake","charging","auxiliary"):
            raise ValueError("Unknown native meter role")
        if self.status not in ("estimated","certified","revised") or self.revision<1:
            raise ValueError("Invalid meter status/revision")
        q,f=D(self.quantity_mwh),D(self.loss_multiplier)
        if q<0 or f<=0 or not q.is_finite() or not f.is_finite():
            raise ValueError("Invalid raw meter/loss multiplier")
        return q*f


def allocate_profile(residual_mwh,weights):
    """Allocate a certified residual to represented profiled loads without leakage."""
    residual=D(residual_mwh)
    if residual<0 or not weights or any(D(v)<0 for v in weights.values()):
        raise ValueError("Invalid profile allocation input")
    total=sum(map(D,weights.values()),D(0))
    if total<=0:
        raise ValueError("Profile weights sum to zero")
    allocated={k:residual*D(v)/total for k,v in weights.items()}
    # Decimal recurring fractions have a final ulp; assign it explicitly.
    last=next(reversed(allocated))
    allocated[last]+=residual-sum(allocated.values(),D(0))
    return allocated


def eligible_fee_base(meters,fee,storage_injection_mwh):
    """Temporary MS charging exemption, retaining auxiliaries and supplier business.

    Input charging quantities must be dedicated eligible storage charging meters;
    supply/auxiliary meters are never absorbed into the charging role. MS 6.1.4
    exempts eligible charging, including conversion losses; exemption is not
    incorrectly capped by subsequent discharge.
    """
    if fee not in ("TUOS","DUOS","RES","PSO","ADMIN"):
        raise ValueError("Unknown fee")
    ordinary=sum((m.adjusted() for m in meters if m.role in ("offtake","auxiliary")),D(0))
    charging=sum((m.adjusted() for m in meters if m.role=="charging"),D(0))
    eligible=charging
    if eligible<0:
        raise ValueError("Negative qualifying storage injection")
    return {"chargeable_mwh":str(ordinary+charging-eligible),
            "exempt_mwh":str(eligible),"formula":"MS6.1.4-7_meter_roles"}


def storage_delivery(configuration,charge_mwh,discharge_mwh,hours,ancillary_mw=None):
    n=len(charge_mwh)
    if len(discharge_mwh)!=n or len(hours)!=n:
        raise ValueError("Storage horizon mismatch")
    soc=D(configuration["initial_soc_mwh"])
    cap=D(configuration["capacity_mwh"]);power=D(configuration["power_mw"])
    eta_c=D(configuration["efficiency_charge"]);eta_d=D(configuration["efficiency_discharge"])
    if not 0<eta_c<=1 or not 0<eta_d<=1 or not 0<=soc<=cap:
        raise ValueError("Invalid storage parameters")
    rows=[]
    for i,(c,d,dt) in enumerate(zip(charge_mwh,discharge_mwh,hours)):
        c,d,dt=D(c),D(d),D(dt)
        reserved=D(ancillary_mw[i] if ancillary_mw is not None else 0)
        if min(c,d,reserved)<0 or dt<=0 or (c>0 and d>0):
            raise ValueError("Invalid/simultaneous storage charging/discharging")
        if configuration.get("embedded_RES",False) and not configuration.get("grid_charging_allowed",False):
            source=configuration.get("grid_charge_mwh")
            if source is None:raise ValueError("Embedded storage requires explicit grid/local charging split")
            if D(source[i])>0:raise ValueError("Embedded configuration prohibits grid charging")
        if max(c,d)/dt+reserved>power:
            raise ValueError("Storage energy/services oversell power")
        initial=soc
        soc+=eta_c*c-d/eta_d
        if not 0<=soc<=cap:
            raise ValueError("Storage SOC infeasible")
        rows.append(dict(period=i,charge_mwh=str(c),discharge_mwh=str(d),initial_soc_mwh=str(initial),
                         final_soc_mwh=str(soc),ancillary_reserved_mw=str(reserved)))
    return rows


def validate_rtbm_eligibility(role,temporary_storage=False):
    if temporary_storage or role=="storage":
        raise ValueError("MS temporary storage is not eligible for ordinary RTBM")
    if role not in ("thermal","dispatchable_RES","dispatchable_load"):
        raise ValueError("Unregistered RTBM role")

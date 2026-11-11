"""Versioned market design. Scenario values and time series belong elsewhere."""
from copy import deepcopy
import json
import math
from pathlib import Path
from .relaxation import SLACK_DEFAULTS,configuration as slack_configuration


CYPRUS_ISOLATED = {
    "id":"cyprus_isolated_v1", "version":"1.0", "zone":"CY",
    "trading_period_minutes":30, "rtbm_period_minutes":5,
    "markets":{"forward":True,"day_ahead":True,"intraday_auctions":False,"balancing":True},
    "interconnections":{"enabled":False},
    "scheduled_isp":{"solve_at":"11:00","effective_from":"12:00"},
    "storage":{"dam":{"enabled":True,"circular_blocks":True},
               "participation_balancing_energy":False,"participation_balancing_capacity":False},
    "res":{"participation_balancing_energy":True,"participation_balancing_capacity":False},
    "slack":deepcopy(SLACK_DEFAULTS),
    "reserve_pricing":"fixed_discrete_lp_shadow_prices",
    "dispatch_equals_delivery":True, "schedule_deviation_cost_eur_mwh":1.,
    "dam_price_floor":0.,"dam_price_cap":1000.,
    "reserve_sustain_hours":.25,
    "settlement":{"method":"cyprus_daily_v1","production_tolerance":.02,
                  "res_tolerance":{"commercial":0.,"grant_supported":0.},
                  "small_supplier_tolerance":.03,"pay_instructed_startups":True},
}


class Ruleset:
    """Configuration with strict supported keys; dict/JSON inputs are copied."""
    def __init__(self,config="cyprus_isolated_v1"):
        if isinstance(config,Ruleset):config=config.to_dict()
        elif isinstance(config,(str,Path)):
            config=deepcopy(CYPRUS_ISOLATED) if str(config)=="cyprus_isolated_v1" else json.loads(Path(config).read_text(encoding="utf-8"))
        if not isinstance(config,dict):raise TypeError("Ruleset must be a dictionary, JSON path or predefined ID")
        def merge(base,changes,path=""):
            for key,value in changes.items():
                if key not in base:raise ValueError(f"Unknown ruleset setting: {path}{key}; time series must be supplied as scenario data")
                if isinstance(base[key],dict):
                    if not isinstance(value,dict):raise TypeError(f"Expected mapping at {path}{key}")
                    merge(base[key],value,path+key+".")
                else:base[key]=deepcopy(value)
        self._data=deepcopy(CYPRUS_ISOLATED);merge(self._data,config)
        d=self._data
        slack_configuration(d['slack'])
        if any(type(v) is not bool for v in d['res'].values()):raise ValueError('RES eligibility must be boolean')
        if d['res']['participation_balancing_capacity']:raise NotImplementedError('RES capacity offers are not supported yet')
        if d["interconnections"]["enabled"] or d["markets"]["intraday_auctions"]:
            raise NotImplementedError("Interconnections and intraday auctions are not implemented in this release")
        if not all(d["markets"][k] for k in ("day_ahead","balancing")):
            raise NotImplementedError("This daily pipeline requires DAM and balancing")
        if (d["trading_period_minutes"],d["rtbm_period_minutes"])!=(30,5):raise ValueError("Supported resolutions are 30-minute MTUs and 5-minute RTUs")
        if d["scheduled_isp"]!={"solve_at":"11:00","effective_from":"12:00"}:raise NotImplementedError("Only the current 11:00/noon scheduled ISP is supported")
        if not d["dispatch_equals_delivery"]:raise NotImplementedError("The public API currently uses instructed dispatch as delivered energy")
        if d["reserve_pricing"]!="fixed_discrete_lp_shadow_prices":raise NotImplementedError("Unsupported reserve-pricing method")
        if d['settlement']['method']!='cyprus_daily_v1':raise NotImplementedError('Unsupported daily settlement implementation')
        for value in ('production_tolerance','small_supplier_tolerance'):
            if not 0<=d['settlement'][value]<1:raise ValueError('Settlement tolerances must be fractions in [0,1)')
        for value in d['settlement']['res_tolerance'].values():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<1:
                raise ValueError('RES tolerances must be finite fractions in [0,1)')
        for key,value in d['markets'].items():
            if type(value) is not bool:raise TypeError(f'Market enabled setting {key} must be boolean')
        for key in ("participation_balancing_energy","participation_balancing_capacity"):
            if type(d["storage"][key]) is not bool:raise TypeError(f"{key} must be a boolean")
        if not all(math.isfinite(d[k]) for k in ('dam_price_floor','dam_price_cap')) or not d["dam_price_floor"]<d["dam_price_cap"]:raise ValueError("Invalid DAM price bounds")
        if d["schedule_deviation_cost_eur_mwh"]<0 or d["reserve_sustain_hours"]<=0:raise ValueError("Invalid scheduling coefficients")
    def to_dict(self):return deepcopy(self._data)
    def __getitem__(self,key):return deepcopy(self._data[key])


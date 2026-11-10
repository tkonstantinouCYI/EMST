"""Public participant/asset API for a single-zone daily market simulation."""
from dataclasses import dataclass,field,asdict
from datetime import date
from copy import deepcopy
from pathlib import Path
import json,math
from .rulesets import Ruleset
from .strategies import Strategy,strategy as make_strategy,STRATEGIES
from .model import PRODUCTS,Forward
from .calendar import day_intervals
from .phases import canonical,profiles as phase_profiles
from .offers import reserve_prices as make_reserve_prices,energy_prices


def _finite_state(values):
    try:valid=all(not isinstance(v,bool) and math.isfinite(v) for v in values)
    except (TypeError,ValueError):valid=False
    if not valid:raise ValueError('Initial-state values must be finite numbers')


@dataclass(frozen=True)
class ThermalState:
    stage: str
    power_mw: float
    time_in_stage_minutes: int


@dataclass(frozen=True)
class StorageState:
    power_mw: float
    soc_mwh: float


@dataclass(frozen=True)
class Participant:
    id: str
    brp: str | None=None
    clearing_member: str | None=None
    strategies: dict=field(default_factory=dict)


@dataclass
class Asset:
    id: str
    participant: str
    technology: str
    strategy: Strategy
    parameters: dict
    initial_state: object=None
    forecast: object=None
    actual: object=None


@dataclass
class MarketResults:
    manifest: dict
    orders: list
    dam: dict
    isp_runs: list
    rtbm: list
    meters: list
    ledger: list
    ending_states: dict
    participant_results: dict
    audit: dict
    inputs: dict=field(default_factory=dict)

    def save(self,directory):
        path=Path(directory);path.mkdir(parents=True,exist_ok=True)
        for name,value in asdict(self).items():
            (path/(name+".json")).write_text(json.dumps(value,indent=2,default=str,allow_nan=False),encoding="utf-8")
        return path

    def to_dataframes(self):
        try:import pandas as pd
        except ImportError:raise ImportError("Install emst-cyprus[dataframes] for DataFrame output") from None
        return {name:pd.DataFrame(getattr(self,name)) for name in ("orders","rtbm","meters","ledger")}


class Market:
    """Owns participants and assets; run inputs contain all time-varying values.

    Forecast and actual RES/load values are MW. Storage bid profiles are MWh per
    MTU. Positive storage power is injection, negative power is withdrawal.
    """
    def __init__(self,ruleset="cyprus_isolated_v1",*,settlement_parameters=None):
        from .history import synthetic_tariffs
        self.ruleset=Ruleset(ruleset);self.participants={};self.assets={};self.forwards=[]
        self.settlement_parameters=deepcopy(settlement_parameters) if settlement_parameters is not None else dict(
            id="synthetic_daily_parameters_v1",tariffs=synthetic_tariffs(),
            synthetic=True)

    @staticmethod
    def time_index(day):
        day=date.fromisoformat(day) if isinstance(day,str) else day
        return [p.start for p in day_intervals(day,30)]

    @staticmethod
    def available_strategies():return deepcopy(STRATEGIES)

    def add_participant(self,participant=None,*,id=None,brp=None,clearing_member=None,strategies=None):
        p=deepcopy(participant) if isinstance(participant,Participant) else Participant(id or participant,brp,clearing_member,deepcopy(strategies or {}))
        if not isinstance(p.id,str) or not p.id or ':' in p.id or p.id in self.participants:raise ValueError("Participant ID must be nonempty, unique and contain no colon")
        if set(p.strategies)-set(STRATEGIES):raise ValueError('Unknown technology in participant strategies')
        for technology,value in p.strategies.items():make_strategy(value,technology)
        self.participants[p.id]=p
        return p

    def _strategy(self,participant,technology,value,parameters):
        if participant not in self.participants:raise ValueError('Register the asset participant first')
        default={'thermal':'marginal_cost','res':'forecast_offer','supplier':'forecast_demand','storage':'fixed_circular'}
        value=value if value is not None else self.participants[participant].strategies.get(technology,default[technology])
        return make_strategy(value,technology,parameters)

    def _add(self,id,participant,technology,bidding,parameters,initial=None,forecast=None,actual=None):
        if not id or id in self.assets:raise ValueError("Asset ID must be nonempty and unique")
        if participant not in self.participants:raise ValueError("Register the asset's participant first")
        for key,value in parameters.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool) and not math.isfinite(value):raise ValueError(f"Invalid asset parameter {key}")
        a=Asset(id,participant,technology,bidding,deepcopy(parameters),initial,forecast,actual)
        self.assets[id]=a
        return a

    def add_thermal(self,id,participant,*,capacity_mw,minimum_mw,marginal_cost,initial_state,
                    ramp_mw_hour=1000.,minimum_up_minutes=60,minimum_down_minutes=60,
                    startup_cost=0.,no_load_cost_per_hour=0.,reserve_cost=2.,reserve_limit_mw=None,
                    startup_minutes=0,shutdown_minutes=0,strategy=None,strategy_parameters=None,
                    energy_up_price=None,energy_down_price=None,reserve_prices=None,phase_profiles_mw=None):
        state=ThermalState(**initial_state) if isinstance(initial_state,dict) else initial_state
        if not isinstance(state,ThermalState):raise ValueError('Thermal initial_state is required')
        stage=canonical(state.stage)
        state=ThermalState(stage,state.power_mw,state.time_in_stage_minutes)
        _finite_state((state.power_mw,state.time_in_stage_minutes))
        if any(a.technology=='thermal' and a.participant==participant for a in self.assets.values()):
            raise ValueError('Each thermal unit must have a different participant')
        if not 0<=minimum_mw<=capacity_mw or ramp_mw_hour<=0:raise ValueError('Invalid thermal capacity/ramp')
        if not 0<=state.power_mw<=capacity_mw or state.time_in_stage_minutes<0:raise ValueError('Invalid previous-day thermal state')
        if stage=='offline' and state.power_mw!=0:raise ValueError('Offline unit must have zero previous power')
        if stage=='dispatch' and state.power_mw<minimum_mw:raise ValueError('Dispatch unit is below minimum output')
        phase_map=phase_profiles(phase_profiles_mw,capacity_mw,minimum_mw,startup_minutes=startup_minutes,shutdown_minutes=shutdown_minutes)
        if stage in phase_map and (not phase_map[stage] or state.time_in_stage_minutes%30 or state.time_in_stage_minutes>=30*len(phase_map[stage])):
            raise ValueError('Initial phase requires an unfinished half-hour profile')
        if min(minimum_up_minutes,minimum_down_minutes,startup_cost,no_load_cost_per_hour,reserve_cost,startup_minutes,shutdown_minutes)<0:raise ValueError("Negative technical/cost parameter")
        if reserve_limit_mw is not None and not 0<=reserve_limit_mw<=capacity_mw:raise ValueError('Invalid reserve limit')
        selected=self._strategy(participant,"thermal",strategy,strategy_parameters)
        bid=selected.price('thermal',marginal_cost,self.ruleset.to_dict())
        energy_prices(bid if energy_up_price is None else energy_up_price,bid if energy_down_price is None else energy_down_price)
        quotes=make_reserve_prices(reserve_cost,reserve_prices)
        return self._add(id,participant,"thermal",selected,dict(
            capacity_mw=capacity_mw,minimum_mw=minimum_mw,marginal_cost=marginal_cost,ramp_mw_hour=ramp_mw_hour,
            minimum_up_minutes=minimum_up_minutes,minimum_down_minutes=minimum_down_minutes,startup_cost=startup_cost,
            no_load_cost_per_hour=no_load_cost_per_hour,reserve_cost=reserve_cost,
            reserve_limit_mw=capacity_mw if reserve_limit_mw is None else reserve_limit_mw,
            startup_minutes=startup_minutes,shutdown_minutes=shutdown_minutes,availability=1.,
            energy_up_price=energy_up_price,energy_down_price=energy_down_price,reserve_prices=quotes,
            phase_profiles_mw=phase_map),state)

    def add_storage(self,id,participant,*,power_mw,capacity_mwh,initial_state,efficiency_charge=.95,
                    efficiency_discharge=.95,ramp_mw_hour=10000.,reserve_cost=1.,degradation_cost=0.,
                    strategy=None,strategy_parameters=None,reserve_prices=None):
        state=StorageState(**initial_state) if isinstance(initial_state,dict) else initial_state
        if not isinstance(state,StorageState):raise ValueError("Storage initial_state needs power_mw and soc_mwh")
        _finite_state((state.power_mw,state.soc_mwh))
        if min(power_mw,capacity_mwh,ramp_mw_hour)<=0 or not 0<efficiency_charge<=1 or not 0<efficiency_discharge<=1:raise ValueError("Invalid storage technical parameters")
        if abs(state.power_mw)>power_mw or not 0<=state.soc_mwh<=capacity_mwh:raise ValueError("Invalid previous-day storage state")
        if reserve_cost<0 or degradation_cost<0:raise ValueError("Negative storage cost")
        selected=self._strategy(participant,'storage',strategy,strategy_parameters)
        values=selected.parameters
        if float(values.get('balancing_up_price',100))<float(values.get('balancing_down_price',0)):
            raise ValueError('Storage upward offer price must not be below its downward price')
        return self._add(id,participant,"storage",selected,dict(
            power_mw=power_mw,capacity_mwh=capacity_mwh,efficiency_charge=efficiency_charge,
            efficiency_discharge=efficiency_discharge,ramp_mw_hour=ramp_mw_hour,reserve_cost=reserve_cost,reserve_prices=make_reserve_prices(reserve_cost,reserve_prices),degradation_cost=degradation_cost),state)

    def add_res(self,id,participant,*,generation_forecast,actual_generation,capacity_mw=None,
                grant_res=False,voltage="MV",strategy=None,strategy_parameters=None,
                energy_down_price=0.,actual_generation_kind="available"):
        if not math.isfinite(energy_down_price):raise ValueError('RES downward price must be finite EUR/MWh')
        if actual_generation_kind not in ('metered','available'):raise ValueError('RES actual_generation_kind is metered or available')
        return self._add(id,participant,"res",self._strategy(participant,"res",strategy,strategy_parameters),dict(
            capacity_mw=capacity_mw,grant_res=grant_res,voltage=voltage,
            energy_down_price=energy_down_price,actual_generation_kind=actual_generation_kind),forecast=generation_forecast,actual=actual_generation)

    def add_supplier(self,id,participant,*,demand_forecast,actual_consumption,voltage="HV",small_supplier=False,
                     strategy=None,strategy_parameters=None):
        if voltage not in ("HV","MV","LV"):raise ValueError("Invalid voltage")
        return self._add(id,participant,"supplier",self._strategy(participant,"supplier",strategy,strategy_parameters),dict(
            voltage=voltage,small_supplier=small_supplier),forecast=demand_forecast,actual=actual_consumption)

    def add_forward(self,contract):
        if not isinstance(contract,Forward):raise TypeError("Expected a Forward input")
        self.forwards.append(contract)

    def run(self,*,time_index,reserve_requirements=None,price_forecast=None,historical_rtbm_prices=None,price_ranges=False,accounting_cycles=False,
            system_forecasts=None,system_observations=None,previous_system_observation=None):
        if accounting_cycles:raise NotImplementedError("The generic participant API currently runs daily settlement; full historical accounting remains available through emst_v03.runner")
        from .market_engine import run_market
        return run_market(self,time_index,reserve_requirements,price_forecast,historical_rtbm_prices,price_ranges,
            system_forecasts,system_observations,previous_system_observation)

    def run_period(self,*,start,end,scenario,checkpoint_directory,resume_from=None):
        """Run inclusive consecutive dates; see docs/13-multi-day-execution.md."""
        from .multiday import run_period
        return run_period(self,start,end,scenario,checkpoint_directory,resume_from)

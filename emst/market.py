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
    economic_owner: str | None=None


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
    diagnostics: dict=field(default_factory=dict)
    economics: dict=field(default_factory=dict)

    def to_evaluation(self):
        from .evaluation import evaluation_result
        return evaluation_result(self)

    def save(self,directory):
        path=Path(directory);path.mkdir(parents=True,exist_ok=True)
        for name,value in asdict(self).items():
            (path/(name+".json")).write_text(json.dumps(value,indent=2,default=str,allow_nan=False),encoding="utf-8")
        return path

    def to_dataframes(self):
        try:import pandas as pd
        except ImportError:raise ImportError("Install emst-cyprus[dataframes] for DataFrame output") from None
        return {name:pd.DataFrame(getattr(self,name)) for name in ("orders","rtbm","meters","ledger")}

    def to_readable(self, *, audience='shared', participant=None):
        from .reporting import readable_results
        return readable_results(self, audience=audience, participant=participant)

    def save_readable(self, directory):
        from .reporting import save_readable_results
        return save_readable_results(self, directory)


class Market:
    """Owns participants and assets; run inputs contain all time-varying values.

    Forecast and actual RES/load values are MW. Storage bid profiles are MWh per
    MTU. Positive storage power is injection, negative power is withdrawal.
    """
    def __init__(self,ruleset="cyprus_isolated_v1",*,settlement_parameters=None,solver_config=None):
        from .solver import configuration
        self.solver_config=configuration(solver_config)
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

    def add_participant(self,participant=None,*,id=None,brp=None,clearing_member=None,strategies=None,economic_owner=None):
        p=deepcopy(participant) if isinstance(participant,Participant) else Participant(id or participant,brp,clearing_member,deepcopy(strategies or {}),economic_owner)
        if not isinstance(p.id,str) or not p.id or ':' in p.id or p.id in self.participants:raise ValueError("Participant ID must be nonempty, unique and contain no colon")
        if p.economic_owner is not None and (not isinstance(p.economic_owner,str) or not p.economic_owner):raise ValueError('economic_owner must be a nonempty string')
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
                    energy_up_price=None,energy_down_price=None,reserve_prices=None,phase_profiles_mw=None,
                    must_run_participation=False,must_run_offer_eur_per_mtu=0.,commitment_model='staged',
                    boundary_power_policy=None,boundary_reserve_policy=None,ramp_up_mw_hour=None,ramp_down_mw_hour=None):
        if commitment_model not in ('staged','on_off'):raise ValueError('commitment_model must be staged or on_off')
        if commitment_model=='on_off':
            if boundary_power_policy not in (None,'technical_minimum') or boundary_reserve_policy not in (None,'none'):
                raise ValueError('ON/OFF supports technical_minimum power and none reserves at boundary intervals')
        elif boundary_power_policy is not None or boundary_reserve_policy is not None or ramp_up_mw_hour is not None or ramp_down_mw_hour is not None:
            raise ValueError('Boundary policies and asymmetric ramps require commitment_model=on_off')
        if type(must_run_participation) is not bool:raise ValueError('must_run_participation must be boolean')
        if isinstance(must_run_offer_eur_per_mtu,bool) or not math.isfinite(must_run_offer_eur_per_mtu) or must_run_offer_eur_per_mtu<0:
            raise ValueError('Must-run offer must be finite nonnegative EUR/unit/MTU')
        if must_run_participation and minimum_mw<=0:raise ValueError('Must-run units require positive minimum MW')
        state=ThermalState(**initial_state) if isinstance(initial_state,dict) else initial_state
        if not isinstance(state,ThermalState):raise ValueError('Thermal initial_state is required')
        stage=canonical(state.stage)
        if commitment_model=='on_off' and stage not in ('dispatch','offline'):raise ValueError('ON/OFF initial stage must be dispatch or offline')
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
        extra={}
        if commitment_model=='on_off':
            ru=ramp_mw_hour if ramp_up_mw_hour is None else ramp_up_mw_hour
            rd=ramp_mw_hour if ramp_down_mw_hour is None else ramp_down_mw_hour
            if any(isinstance(x,bool) or not math.isfinite(x) or x<=0 for x in (ru,rd)):raise ValueError('ON/OFF directional ramps must be finite positive')
            extra=dict(commitment_model='on_off',boundary_power_policy='technical_minimum',boundary_reserve_policy='none',
                ramp_up_mw_hour=ru,ramp_down_mw_hour=rd,plant_representation='single_envelope')
        return self._add(id,participant,"thermal",selected,dict(**extra,
            capacity_mw=capacity_mw,minimum_mw=minimum_mw,marginal_cost=marginal_cost,ramp_mw_hour=ramp_mw_hour,
            minimum_up_minutes=minimum_up_minutes,minimum_down_minutes=minimum_down_minutes,startup_cost=startup_cost,
            no_load_cost_per_hour=no_load_cost_per_hour,reserve_cost=reserve_cost,
            reserve_limit_mw=capacity_mw if reserve_limit_mw is None else reserve_limit_mw,
            startup_minutes=startup_minutes,shutdown_minutes=shutdown_minutes,availability=1.,
            energy_up_price=energy_up_price,energy_down_price=energy_down_price,reserve_prices=quotes,
            phase_profiles_mw=phase_map,must_run_participation=must_run_participation,
            must_run_offer_eur_per_mtu=must_run_offer_eur_per_mtu),state)

    def add_configured_thermal(self,id,participant,*,components,configurations,transitions,
            initial_state,dam_price=None,energy_up_price=None,energy_down_price=None,
            reserve_cost=2.,reserve_prices=None,strategy=None,strategy_parameters=None,configuration_formulation='reference'):
        """Register one plant; configuration alternatives are internal, never assets.

        Transition profiles and component membership are explicit half-hour steps.
        Simple DAM orders use the plant maximum and dam_price (default: lowest
        operating configuration marginal cost). Configuration costs affect ISP.
        """
        from .configurations import validate
        if configuration_formulation not in ('reference','compact'):raise ValueError('configuration_formulation must be reference or compact')
        data,state=validate(components,configurations,transitions,initial_state)
        configs=list(data['configurations'].values());operating=[c for name,c in data['configurations'].items() if name!='OFF']
        if not operating:raise ValueError('At least one operating configuration is required')
        base=min(c['marginal_cost'] for c in operating)
        if dam_price is not None:
            if strategy is not None or strategy_parameters:raise ValueError('Choose dam_price or a thermal strategy')
            strategy='fixed_markup';strategy_parameters={'markup_eur_mwh':float(dam_price)-base}
        # Use the normal registration checks and common plant-level market API.
        placeholder=ThermalState('offline',0,0)
        a=self.add_thermal(id,participant,capacity_mw=max(c['maximum_mw'] for c in configs),
            minimum_mw=0,marginal_cost=base,initial_state=placeholder,
            minimum_up_minutes=0,minimum_down_minutes=0,ramp_mw_hour=max(c['ramp_mw_hour'] for c in configs),
            reserve_limit_mw=max(c['reserve_limit_mw'] for c in configs),reserve_cost=reserve_cost,
            energy_up_price=energy_up_price,energy_down_price=energy_down_price,reserve_prices=reserve_prices,
            strategy=strategy,strategy_parameters=strategy_parameters)
        a.parameters['plant_configurations']=data;a.parameters['configuration_formulation']=configuration_formulation;a.initial_state=state
        return a

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
        if values.get('terminal_soc_mwh',state.soc_mwh)>capacity_mwh:raise ValueError('Strategy terminal SOC exceeds storage capacity')
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
            system_forecasts=None,system_observations=None,previous_system_observation=None,
            must_run_requirement_mw=None,must_run_supplier_shares=None,actual_balance_tolerance_mwh=1e-6,
            storage_terminal_soc_mwh=None,rtbm_interval_minutes=5,isp_warm_start=None,isp_interval_minutes=30):
        if accounting_cycles:raise NotImplementedError("The generic participant API currently runs daily settlement; full historical accounting remains available through emst.runner")
        from .resolution import validate_minutes
        validate_minutes(rtbm_interval_minutes)
        from .market_engine import run_market
        from .solver import session
        from .evaluation import enrich,residuals
        residuals([],actual_balance_tolerance_mwh)
        if isp_warm_start is not None:
            from .warm_start import validate_seed
            validate_seed(isp_warm_start)
        for asset in self.assets.values():asset.strategy.validate(asset.technology)
        with session(self.solver_config) as context:
            result=run_market(self,time_index,reserve_requirements,price_forecast,historical_rtbm_prices,price_ranges,
                system_forecasts,system_observations,previous_system_observation,must_run_requirement_mw,must_run_supplier_shares,
                storage_terminal_soc_mwh,rtbm_interval_minutes,isp_warm_start,isp_interval_minutes)
        return enrich(result,self,context['records'],actual_balance_tolerance_mwh)

    def evaluate(self,**inputs):
        """JSON-safe success/failure contract; numerical failures are never economic penalties."""
        from .solver import SolveError
        try:return self.run(**inputs).to_evaluation()
        except (ValueError,TypeError,NotImplementedError,SolveError,RuntimeError) as error:
            kind='unsupported_feature' if isinstance(error,NotImplementedError) else 'invalid_input' if isinstance(error,(ValueError,TypeError)) else 'model_or_numerical_failure'
            if isinstance(error,SolveError):
                kind={'time_limit':'solver_limit','iteration_limit':'solver_limit','solver_limit':'solver_limit',
                    'infeasible':'optimization_infeasible','unbounded':'optimization_unbounded'}.get(error.record['termination_reason'],'solver_failure')
            return dict(schema_version='1.0',status='failed',failure=dict(kind=kind,message=str(error),
                solve_record=error.record if isinstance(error,SolveError) else None))

    def run_period(self,*,start,end,scenario,checkpoint_directory,resume_from=None,must_run_auction_frequency="monthly"):
        """Run inclusive consecutive dates; see docs/multi-day.md."""
        from .multiday import run_period
        return run_period(self,start,end,scenario,checkpoint_directory,resume_from,must_run_auction_frequency)

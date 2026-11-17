"""Strategies generate offers; they cannot alter clearing or settlement rules."""
from dataclasses import dataclass,field
from copy import deepcopy
import math


STRATEGIES={"thermal":("marginal_cost","fixed_markup"),"res":("forecast_offer",),
            "supplier":("forecast_demand","price_responsive"),"storage":("fixed_circular","forecast_arbitrage")}


PARAMETERS={
    'marginal_cost':set(), 'fixed_markup':{'markup_eur_mwh'},
    'forecast_offer':{'price_eur_mwh'},'forecast_demand':{'price_eur_mwh'},'price_responsive':{'price_eur_mwh'},
    'fixed_circular':{'buy_mwh','sell_mwh','buy_price','sell_price','balancing_up_price','balancing_down_price'},
    'forecast_arbitrage':{'terminal_soc_mwh','buy_price','sell_price','balancing_up_price','balancing_down_price'},
}

@dataclass(frozen=True)
class Strategy:
    name: str
    parameters: dict=field(default_factory=dict)

    def validate(self,technology):
        if self.name not in STRATEGIES[technology]:raise ValueError(f"Strategy {self.name!r} is not available for {technology}: {STRATEGIES[technology]}")

        if not isinstance(self.parameters,dict):raise TypeError('Strategy parameters must be a mapping')
        unknown=set(self.parameters)-PARAMETERS[self.name]
        if unknown:raise ValueError(f'Unknown strategy parameters for {self.name}: {sorted(unknown)}')
        for name,value in self.parameters.items():
            if name in ('buy_mwh','sell_mwh'):continue  # Timestamp/shape/physical validation occurs at the run gate.
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(f'Strategy parameter {name} must be finite numeric')
            if name=='terminal_soc_mwh' and value<0:raise ValueError('Terminal SOC must be nonnegative')

    def price(self,technology,cost,rules):
        self.validate(technology)
        if technology=="thermal":return cost+float(self.parameters.get("markup_eur_mwh",0)) if self.name=="fixed_markup" else cost
        if technology=="res":return float(self.parameters.get("price_eur_mwh",rules["dam_price_floor"]))
        if technology=="supplier":return float(self.parameters.get("price_eur_mwh",rules["dam_price_cap"]))
        raise ValueError("Storage strategies produce linked multi-period orders")


def strategy(value,technology,parameters=None):
    if isinstance(value,Strategy) and parameters is not None:raise ValueError('Put strategy parameters in the Strategy instance or separate argument, not both')
    result=deepcopy(value) if isinstance(value,Strategy) else Strategy(value,deepcopy(parameters or {}))
    result.validate(technology)
    return result

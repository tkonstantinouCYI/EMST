"""Strategies generate offers; they cannot alter clearing or settlement rules."""
from dataclasses import dataclass,field


STRATEGIES={"thermal":("marginal_cost","fixed_markup"),"res":("forecast_offer",),
            "supplier":("forecast_demand","price_responsive"),"storage":("fixed_circular","forecast_arbitrage")}


@dataclass(frozen=True)
class Strategy:
    name: str
    parameters: dict=field(default_factory=dict)

    def validate(self,technology):
        if self.name not in STRATEGIES[technology]:raise ValueError(f"Strategy {self.name!r} is not available for {technology}: {STRATEGIES[technology]}")

    def price(self,technology,cost,rules):
        self.validate(technology)
        if technology=="thermal":return cost+float(self.parameters.get("markup_eur_mwh",0)) if self.name=="fixed_markup" else cost
        if technology=="res":return float(self.parameters.get("price_eur_mwh",rules["dam_price_floor"]))
        if technology=="supplier":return float(self.parameters.get("price_eur_mwh",rules["dam_price_cap"]))
        raise ValueError("Storage strategies produce linked multi-period orders")


def strategy(value,technology,parameters=None):
    result=value if isinstance(value,Strategy) else Strategy(value,parameters or {})
    result.validate(technology)
    return result

"""Dimensioned input records. Monetary parameters are explicitly synthetic."""
from dataclasses import dataclass, field
from datetime import datetime
import math

PRODUCTS = ("FCR_up", "FCR_down", "aFRR_up", "aFRR_down", "mFRR_up", "mFRR_down", "RR_up")
UP = ("FCR_up", "aFRR_up", "mFRR_up", "RR_up")
DOWN = ("FCR_down", "aFRR_down", "mFRR_down")
RESPONSE_HOURS = {"FCR_up":.5/60,"FCR_down":.5/60,"aFRR_up":5/60,"aFRR_down":5/60,
                  "mFRR_up":12.5/60,"mFRR_down":12.5/60,"RR_up":.5}


@dataclass(frozen=True)
class Unit:
    id: str
    participant: str
    maximum_mw: float
    minimum_mw: float
    energy_eur_mwh: float
    ramp_mw_hour: float
    initial_mw: float
    initial_on: int
    initial_duration: int
    min_up: int = 2
    min_down: int = 2
    startup_eur: float = 100
    no_load_eur_period: float = 5
    agc_min_mw: float = 0
    agc_max_mw: float = 1e6
    reserve_eur_mw_period: dict = field(default_factory=lambda: {p: 2 for p in PRODUCTS})
    reserve_limit_mw: float = 30
    startup_limit_mw: float = 30
    shutdown_limit_mw: float = 30
    hot_start_hours: float = 4
    warm_start_hours: float = 12
    warm_start_multiplier: float = 1.3
    cold_start_multiplier: float = 1.8
    soak_periods: int = 0
    startup_trajectory_mw: tuple = ()
    shutdown_trajectory_mw: tuple = ()
    energy_up_eur_mwh: float | None = None
    energy_down_eur_mwh: float | None = None
    phase_profiles_mw: dict | None = None

    def validate(self):
        from .offers import energy_prices
        energy_prices(self.energy_eur_mwh if self.energy_up_eur_mwh is None else self.energy_up_eur_mwh,
                      self.energy_eur_mwh if self.energy_down_eur_mwh is None else self.energy_down_eur_mwh)
        if not (0 <= self.minimum_mw <= self.maximum_mw):
            raise ValueError(f"Invalid capacity {self.id}")
        if self.ramp_mw_hour <= 0 or self.min_up < 1 or self.min_down < 1:
            raise ValueError(f"Invalid dynamics {self.id}")
        if self.initial_on not in (0, 1) or self.initial_duration < 1:
            raise ValueError("Initial commitment history is required")
        if not (0 <= self.initial_mw <= self.maximum_mw):
            raise ValueError("Invalid initial output")
        if not self.initial_on and self.initial_mw:
            raise ValueError("Offline initial state has nonzero output")
        if set(self.reserve_eur_mw_period) != set(PRODUCTS):
            raise ValueError("All reserve quote units/rates must be supplied")
        if self.soak_periods<0 or len(self.startup_trajectory_mw)!=self.soak_periods:
            raise ValueError("Each soaking phase needs an explicit endpoint power")
        if any(not 0<=v<=self.maximum_mw for v in self.startup_trajectory_mw):
            raise ValueError("Invalid startup trajectory")
        if self.startup_trajectory_mw and self.startup_trajectory_mw[-1]<self.minimum_mw:
            raise ValueError("Startup trajectory must reach stable output")
        if self.shutdown_trajectory_mw and self.shutdown_trajectory_mw[-1]!=0:
            raise ValueError("Shutdown trajectory must finish at zero")
        from .phases import unit_profiles
        unit_profiles(self)


@dataclass(frozen=True)
class Forecast:
    issued_at: datetime
    available_at: datetime
    values_mw: tuple
    version: str

    def at_cutoff(self, cutoff):
        if self.available_at > cutoff or self.issued_at > cutoff:
            raise ValueError(f"Forecast {self.version} is not yet available")
        if any(not math.isfinite(x) or x < 0 for x in self.values_mw):
            raise ValueError("Forecast must be finite nonnegative MW")
        return self.values_mw


@dataclass(frozen=True)
class Forward:
    id: str
    seller: str
    buyer: str
    asset: str
    energy_mwh: tuple
    price_eur_mwh: float | None
    version: int = 1
    buyer_entity: str | None = None


@dataclass(frozen=True)
class Order:
    id: str
    participant: str
    asset: str
    period: int
    side: str
    energy_mwh: float
    price_eur_mwh: float
    block: str | None = None
    minimum_acceptance: float = 0
    parent: str | None = None
    exclusive_group: str | None = None
    circular: bool = False


@dataclass(frozen=True)
class Registration:
    entity: str
    participant: str
    brp: str
    member: str
    meter: str
    role: str
    voltage: str
    tolerance: float = .02
    small_supplier: bool = False
    agc: bool = False
    grant_res: bool = False
    consumption_share: float = 0


def finite(value, name):
    if not math.isfinite(value):
        raise ValueError(f"Nonfinite {name}")
    return value

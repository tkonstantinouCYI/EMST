"""TSO scenario inputs and causal, completed-interval observation updates."""
import math
from .model import PRODUCTS
from .series import mw_values


def reserve_series(values, index):
    """Accept flat product names or {FCR/aFRR/mFRR: {up/down: series}}."""
    flat = {}
    for product, series in (values or {}).items():
        if product in ('FCR', 'aFRR', 'mFRR'):
            if not isinstance(series, dict) or set(series) != {'up', 'down'}:
                raise ValueError(f'{product} requires both up and down requirement series')
            entries = {f'{product}_{direction}': data for direction, data in series.items()}
        elif product in PRODUCTS:
            entries = {product: series}
        else:
            raise ValueError(f'Unknown reserve product {product}')
        if set(flat) & set(entries):
            raise ValueError('Duplicate reserve requirement product/direction')
        flat.update(entries)
    return {p: mw_values(flat.get(p, [0.] * len(index)), index, p + ' requirements') for p in PRODUCTS}


def system_series(values, mtu_index, rtu_index, *, name, fallback=None):
    """Normalise independent demand/RES MW arrays to MTUs and RTUs."""
    source = 'explicit_system_input'
    if values is None:
        if fallback is None:
            raise ValueError(f'{name} requires demand and res series')
        values = fallback
        source = 'aggregate_participant_series'
    if not isinstance(values, dict) or set(values) != {'demand', 'res'}:
        raise ValueError(f'{name} requires exactly demand and res keys')
    mtu, rtu = {}, {}
    for key, data in values.items():
        if len(data) == len(mtu_index):
            mtu[key] = mw_values(data, mtu_index, name + ' ' + key)
            rtu[key] = [x for x in mtu[key] for _ in range(6)]
        else:
            rtu[key] = mw_values(data, rtu_index, name + ' ' + key)
            mtu[key] = [sum(rtu[key][i:i+6])/6 for i in range(0, len(rtu_index), 6)]
    return mtu, rtu, source


def observation_update(demand, res, *, previous=None):
    """Persist the latest observed error; never read the current actual value.

    previous contains the completed interval's measurement AND its uncorrected
    TSO forecast. Comparing with that baseline avoids recursively cancelling or
    accumulating corrections. Clipping is explicit and exported.
    """
    baseline = {'demand': float(demand), 'res': float(res)}
    if not all(math.isfinite(x) and x >= 0 for x in baseline.values()):
        raise ValueError('Invalid TSO baseline forecast')
    errors = {'demand': 0., 'res': 0.}
    if previous is not None:
        required = {'demand_mw', 'res_mw', 'forecast_demand_mw', 'forecast_res_mw'}
        if set(previous) != required or not all(math.isfinite(float(v)) and float(v) >= 0 for v in previous.values()):
            raise ValueError('Previous observation requires finite nonnegative demand/res and baseline forecasts')
        errors = {k: float(previous[k + '_mw']) - float(previous['forecast_' + k + '_mw']) for k in baseline}
    prediction = {k: max(0., baseline[k] + errors[k]) for k in baseline}
    return dict(baseline_demand_mw=baseline['demand'], baseline_res_mw=baseline['res'],
                demand_prediction_mw=prediction['demand'], res_prediction_mw=prediction['res'],
                demand_error_mw=errors['demand'], res_error_mw=errors['res'],
                net_demand_prediction_mw=prediction['demand']-prediction['res'],
                clipped={k: baseline[k] + errors[k] < 0 for k in baseline})

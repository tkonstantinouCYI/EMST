"""Dimensioned ISP offer prices, independent of DAM and settlement prices."""
import math
from .model import PRODUCTS


def energy_prices(up,down):
    up,down=float(up),float(down)
    if not all(math.isfinite(x) for x in (up,down)) or up<down:
        raise ValueError('ISP energy prices must be finite and upward price >= downward price')
    return up,down


def reserve_prices(default,overrides=None):
    result={p:float(default) for p in PRODUCTS};seen=set()
    for product,value in (overrides or {}).items():
        if product in ('FCR','aFRR','mFRR'):
            if not isinstance(value,dict) or set(value)!={'up','down'}:
                raise ValueError('Reserve offer requires up and down prices')
            entries={product+'_'+direction:price for direction,price in value.items()}
        elif product in PRODUCTS:entries={product:value}
        else:raise ValueError('Unknown reserve offer product')
        if seen & entries.keys():raise ValueError('Duplicate reserve offer price')
        seen.update(entries);result.update({p:float(v) for p,v in entries.items()})
    if not all(math.isfinite(x) and x>=0 for x in result.values()):
        raise ValueError('Reserve offer prices must be finite nonnegative EUR/MW/MTU')
    return result


def asset_energy_prices(asset,rules):
    if asset.technology=='thermal':
        bid=asset.strategy.price('thermal',asset.parameters['marginal_cost'],rules)
        up=asset.parameters.get('energy_up_price');down=asset.parameters.get('energy_down_price')
        return energy_prices(bid if up is None else up,bid if down is None else down)
    if asset.technology=='res':return (0.,float(asset.parameters['energy_down_price']))
    return energy_prices(asset.strategy.parameters.get('balancing_up_price',100),
                         asset.strategy.parameters.get('balancing_down_price',0))

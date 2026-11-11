"""Explicit diagnostic slack quantities and synthetic configurable penalties."""
from copy import deepcopy
import math
import pulp as lp
from .model import PRODUCTS

SLACK_DEFAULTS=dict(enabled=True,energy_shortage_eur_mwh=10000.,energy_surplus_eur_mwh=10000.,
                    reserve_shortage_eur_mw_mtu={p:10000. for p in PRODUCTS})


def configuration(values=None):
    result=deepcopy(SLACK_DEFAULTS)
    for key,value in (values or {}).items():
        if key not in result:raise ValueError('Unknown slack parameter '+key)
        if key=='reserve_shortage_eur_mw_mtu':
            if not isinstance(value,dict) or set(value)-set(PRODUCTS):raise ValueError('Unknown slack reserve product')
            result[key].update(value)
        else:result[key]=value
    if type(result['enabled']) is not bool:raise ValueError('Slack enabled must be boolean')
    prices=[result['energy_shortage_eur_mwh'],result['energy_surplus_eur_mwh'],*result['reserve_shortage_eur_mw_mtu'].values()]
    if any(isinstance(x,bool) or not math.isfinite(x) or x<=0 for x in prices):raise ValueError('Slack penalties must be finite positive prices')
    return result


def build(intervals,values=None,*,include_capacity=True):
    config=configuration(values);bound=None if config['enabled'] else 0
    shortage={j:lp.LpVariable(f'energy_shortage_mwh_{j}',0,bound) for j in range(len(intervals))}
    surplus={j:lp.LpVariable(f'energy_surplus_mwh_{j}',0,bound) for j in range(len(intervals))}
    reserve={(p,j):lp.LpVariable(f'reserve_shortage_mw_{p}_{j}',0,bound) for p in (PRODUCTS if include_capacity else ()) for j in range(len(intervals))}
    costs=dict(energy_shortage_penalty=lp.lpSum(v*config['energy_shortage_eur_mwh'] for v in shortage.values()),
               energy_surplus_penalty=lp.lpSum(v*config['energy_surplus_eur_mwh'] for v in surplus.values()),
               reserve_shortage_penalty=lp.lpSum(v*config['reserve_shortage_eur_mw_mtu'][p]*(intervals[j].hours/.5) for (p,j),v in reserve.items()))
    if not include_capacity:costs.pop("reserve_shortage_penalty")
    return shortage,surplus,reserve,costs


def quantities(j,shortage,surplus,reserve):
    return dict(energy_shortage_mwh=float(shortage[j].value() or 0),energy_surplus_mwh=float(surplus[j].value() or 0),
                reserve_shortage_mw={p:float(reserve[p,j].value() or 0) for p in PRODUCTS if (p,j) in reserve})


def used(rows,tolerance=1e-6):
    return any(r['energy_shortage_mwh']>tolerance or r['energy_surplus_mwh']>tolerance or
               any(x>tolerance for x in r['reserve_shortage_mw'].values()) for r in rows)

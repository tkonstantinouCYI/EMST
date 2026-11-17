"""Explicit sequential RTBM resolution and historical-price normalization."""
import math


def validate_minutes(value):
    if type(value) is not int or value not in (5,30):
        raise ValueError('rtbm_interval_minutes must be 5 or 30')
    return value


def aggregate_six(values):
    return [sum(values[i:i+6])/6 for i in range(0,len(values),6)]


def history_sets(value,intervals_per_mtu):
    """Coarse history accepts native MTU prices or averages measured 5-min sets."""
    if len(value)!=3:raise ValueError('Three corresponding historical price days are required')
    result=[]
    for day in value:
        prices=[float(v) for v in day]
        if any(not math.isfinite(v) for v in prices):raise ValueError('Historical prices must be finite')
        if len(prices)==intervals_per_mtu:result.append(prices)
        elif intervals_per_mtu==1 and len(prices)==6:result.append([sum(prices)/6])
        else:raise ValueError(f'Historical prices need {intervals_per_mtu} prices per MTU (or six to aggregate in 30-minute mode)')
    return result

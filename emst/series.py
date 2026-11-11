"""MW time series from arrays or indexed Series/single-column DataFrames."""
from datetime import datetime
import math


def timestamps(index):
    result=[]
    for value in index:
        if hasattr(value,"to_pydatetime"):value=value.to_pydatetime()
        if isinstance(value,str):value=datetime.fromisoformat(value)
        if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError("Time indices require timezone-aware timestamps")
        result.append(value)
    if len(set(result))!=len(result) or any(b<=a for a,b in zip(result,result[1:])):raise ValueError("Time index must be strictly increasing and unique")
    return result


def mw_values(data,index,label):
    """Never silently align, interpolate, fill gaps or broadcast scalar inputs."""
    if data is None:raise ValueError(f"Missing {label}")
    if hasattr(data,"to_numpy") and hasattr(data,"index"):
        if timestamps(data.index)!=list(index):raise ValueError(f"{label}: timestamp index mismatch")
        raw=data.to_numpy()
        if getattr(raw,"ndim",1)==2:
            if raw.shape[1]!=1:raise ValueError(f"{label}: provide a Series or single-column DataFrame")
            raw=raw[:,0]
    else:raw=data
    try:values=[float(v) for v in raw]
    except (TypeError,ValueError):raise ValueError(f"{label}: expected a numeric array") from None
    if len(values)!=len(index) or any(not math.isfinite(v) or v<0 for v in values):raise ValueError(f"{label}: require {len(index)} finite nonnegative MW values")
    return values


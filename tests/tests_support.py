def without_runtime(value):
    """Compare all economics, feasibility, solver statuses and provenance, not clocks."""
    if isinstance(value,dict):
        return {k:without_runtime(v) for k,v in value.items()
                if k not in ('wall_seconds','solver_wall_seconds_by_stage')}
    if isinstance(value,list):return [without_runtime(v) for v in value]
    return value

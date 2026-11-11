"""Shared dimensioned thermal trajectory kernel for ISP and RTBM adapters."""
import pulp as lp


def thermal_path(model, name, initial_mw, hours, segments, capacity_mw,
                 operating, minimum_mw, ramp_mw_hour, *, upward_headroom=0,
                 downward_headroom=0):
    """Piecewise linear power, exact trapezoidal MWh, bounded node ramps.

    The initial node is inherited telemetry and is never retroactively changed.
    Capacity/headroom bounds apply to future nodes. ISP uses two segments;
    five-minute RTBM uses ten 30-second segments, including ramps and holds.
    Prescribed thermal phases add linear-path constraints in their adapters.
    Commitment and phase constraints are supplied by each market adapter.
    """
    if segments < 1 or hours <= 0:
        raise ValueError('Invalid trajectory resolution')
    nodes = [initial_mw] + [lp.LpVariable(f'{name}_node_{k}', 0, capacity_mw) for k in range(1, segments+1)]
    step = hours / segments
    for previous, current in zip(nodes, nodes[1:]):
        model += current <= capacity_mw * operating - upward_headroom
        model += current >= minimum_mw + downward_headroom
        model += current - previous <= ramp_mw_hour * step
        model += previous - current <= ramp_mw_hour * step
    energy = step * (.5*nodes[0] + lp.lpSum(nodes[1:-1]) + .5*nodes[-1])
    return nodes, energy

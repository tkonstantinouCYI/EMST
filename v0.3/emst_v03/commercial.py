"""Forward notification validation and single-zone, energy-denominated DAM."""
from itertools import product
import math
import pulp as lp
from .optimization import solve, dual_face


def forward_schedules(contracts, registrations, units, count):
    ids = set()
    schedules = {r.entity: [0.0]*count for r in registrations.values()}
    cash_known = True
    for c in contracts:
        if c.id in ids or c.version < 1:
            raise ValueError("Duplicate/invalid forward notification version")
        ids.add(c.id)
        if c.asset not in units or units[c.asset].participant != c.seller:
            raise ValueError("Forward physical allocation does not match seller")
        eligible=[r.entity for r in registrations.values() if r.participant==c.buyer and r.role=="offtake"]
        target=c.buyer_entity
        if target is None:
            if len(eligible)!=1:raise ValueError("Forward buyer requires an explicit eligible buyer_entity allocation")
            target=eligible[0]
        if target not in eligible:
            raise ValueError("Forward buyer_entity is not an offtake registration owned by buyer participant")
        if len(c.energy_mwh) != count:
            raise ValueError("Forward delivery period mismatch")
        for t, q in enumerate(c.energy_mwh):
            if not math.isfinite(q) or q < 0 or q > units[c.asset].maximum_mw*.5:
                raise ValueError("Invalid forward MWh notification")
            schedules[c.asset][t] += q
            schedules[target][t] += q  # native positive-consumption allocation
        if c.price_eur_mwh is None:
            cash_known = False
        elif not math.isfinite(c.price_eur_mwh):
            raise ValueError("Invalid private contract price")
    for t in range(count):
        for entity,u in units.items():
            if entity in schedules and schedules[entity][t]>u.maximum_mw*.5+1e-7:
                raise ValueError("Aggregate forward physical allocation exceeds registered capacity")
        supply = sum(v[t] for e,v in schedules.items() if registrations[e].role == "production")
        demand = sum(v[t] for e,v in schedules.items() if registrations[e].role == "offtake")
        if abs(supply-demand) > 1e-7:
            raise ValueError("Unmatched forward notifications")
    return schedules, cash_known


def clear_dam(orders, count, floor=0, cap=1000,capacity_margins=None):
    """Enumerate discrete block regimes, solve each continuous welfare problem.

    Enforce price-compatible accepted blocks on the common dual face. This exact
    small-book implementation deliberately rejects books with >10 block groups.
    Divisible linked legs share the block acceptance ratio. Exclusive groups are
    available for mutually exclusive circular alternatives.
    """
    seen = set()
    for o in orders:
        if o.id in seen or o.side not in ("buy", "sell"):
            raise ValueError("Duplicate order ID or invalid side")
        seen.add(o.id)
        if not (0 <= o.period < count and math.isfinite(o.energy_mwh) and o.energy_mwh >= 0):
            raise ValueError("Invalid order energy/period")
        if not (floor <= o.price_eur_mwh <= cap and 0 <= o.minimum_acceptance <= 1):
            raise ValueError("Invalid order bound/minimum acceptance")
        if o.minimum_acceptance and not o.block:
            raise ValueError("Minimum acceptance needs a block ID")
        if (o.parent or o.exclusive_group) and not o.block:
            raise ValueError("Linked/exclusive alternatives require a block ID")
    blocks = sorted({o.block for o in orders if o.block})
    if len(blocks) > 10:
        raise ValueError("Exact block enumeration limited to 10 groups")
    if any(o.parent and o.parent not in blocks for o in orders):
        raise ValueError("Unknown linked parent")
    for b in blocks:
        legs=[o for o in orders if o.block==b]
        if len({(o.participant,o.parent,o.exclusive_group,o.circular) for o in legs})!=1 or (not legs[0].circular and len({o.side for o in legs})!=1):
            raise ValueError("Block legs disagree on owner/side/links")
    ancestors={b:next(o.parent for o in orders if o.block==b) for b in blocks}
    for b in blocks:
        chain=set();cursor=b
        while cursor is not None:
            if cursor in chain:
                raise ValueError("Cyclic linked-block parent relationship")
            chain.add(cursor);cursor=ancestors[cursor]
    best = None
    branches = 0
    for bits in product((0,1), repeat=len(blocks)):
        active = dict(zip(blocks, bits))
        if any(o.parent and active[o.block] > active[o.parent] for o in orders):
            continue
        exclusive = {}
        for o in orders:
            if o.exclusive_group and o.block and active[o.block]:
                exclusive.setdefault(o.exclusive_group,set()).add(o.block)
        if any(len(v)>1 for v in exclusive.values()):
            continue
        m = lp.LpProblem("DAM", lp.LpMinimize)
        ratios = {b:lp.LpVariable(f"block_{i}", lowBound=max(o.minimum_acceptance for o in orders if o.block==b) if active[b] else 0,
                                upBound=active[b]) for i,b in enumerate(blocks)}
        for b,parent in ancestors.items():
            if parent:m += ratios[b]<=ratios[parent]
        accepted = {}
        for i,o in enumerate(orders):
            accepted[o.id] = ratios[o.block]*o.energy_mwh if o.block else lp.LpVariable(f"q_{i}",0,o.energy_mwh)
        m += lp.lpSum((1 if o.side=="sell" else -1)*o.price_eur_mwh*accepted[o.id] for o in orders)
        for t in range(count):
            m += lp.lpSum((1 if o.side=="sell" else -1)*accepted[o.id] for o in orders if o.period==t)==0, f"balance_{t}"
        for (asset,t),margin in (capacity_margins or {}).items():
            if margin<0:raise ValueError("Forward schedules leave a negative eligible selling margin")
            m += lp.lpSum(accepted[o.id] for o in orders if o.asset==asset and o.period==t and o.side=="sell") <= margin
        try:
            objective = solve(m,False)
        except RuntimeError:
            continue
        branches += 1
        if best and objective > best[0]+1e-6:
            continue
        def block_surplus(face,y):
            for b in blocks:
                if active[b]:
                    descendants={b}
                    for candidate in blocks:
                        cursor=candidate
                        while cursor is not None:
                            if cursor==b:descendants.add(candidate);break
                            cursor=ancestors[cursor]
                    face += lp.lpSum((1 if o.side=="sell" else -1)*o.energy_mwh*(y[f"balance_{o.period}"]-o.price_eur_mwh)
                                     for o in orders if o.block in descendants and active[o.block]) >= 0
            for t in range(count):
                face += y[f"balance_{t}"] >= floor
                face += y[f"balance_{t}"] <= cap
        try:
            prices,_ = dual_face(m,[f"balance_{t}" for t in range(count)],False,block_surplus)
        except RuntimeError:
            continue
        values = {o.id:float(lp.value(accepted[o.id]) or 0) for o in orders}
        candidate = (objective, values, [prices[f"balance_{t}"] for t in range(count)], active)
        if best is None or objective < best[0]-1e-6:
            best = candidate
    if best is None:
        raise RuntimeError("No price-compatible feasible DAM branch")
    return {"objective_eur":best[0], "welfare_eur":-best[0], "accepted_mwh":best[1],
            "prices_eur_mwh":best[2], "blocks":best[3], "branches_solved":branches,
            "status":"Optimal", "pricing":"joint_dual_face_lexicographic",
            "cash_balance_tolerance_eur":.005 if isinstance(m.solver,lp.PULP_CBC_CMD) else 1e-6}

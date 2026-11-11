"""Checked CBC solves and joint optimal-dual-face pricing for minimisation LPs."""
import pulp as lp
try:
    import highspy
except ImportError:
    highspy = None

EPS = 1e-6


def solve(problem, mip=True):
    backend = (lp.HiGHS(msg=False,mip=mip,gapRel=0,threads=1,timeLimit=90)
               if highspy is not None else
               lp.PULP_CBC_CMD(msg=False,mip=mip,gapRel=0,threads=1,timeLimit=90))
    status = problem.solve(backend)
    if status != lp.LpStatusOptimal or problem.sol_status != lp.LpSolutionOptimal:
        raise RuntimeError(f"{problem.name}: {lp.LpStatus[status]}")
    return float(lp.value(problem.objective) or 0)


def freeze_discrete(problem):
    frozen = {}
    for var in problem.variables():
        if var.cat != lp.LpContinuous:
            value = round(var.value())
            if abs(var.value()-value) > EPS:
                raise RuntimeError("Nonintegral certified discrete decision")
            frozen[var.name] = value
            var.lowBound = var.upBound = value
            var.cat = lp.LpContinuous
    return frozen


def lp_optimal_value(problem):
    """Recover the variable objective from optimal duals and original RHS/bounds.

    CBC rounds exported primal values; summing costs from those rounded values
    can put a subsequent objective-preservation equality off the optimal face.
    """
    value=sum(-c.constant*c.pi for c in problem.constraints.values())
    for var in problem.variables():
        reduced=var.dj or 0.
        bound=var.lowBound if reduced>=0 else var.upBound
        if bound is not None:value+=reduced*bound
    return float(value)


def dual_face(problem, names, ranges=True, extra=None, select=True):
    """Create the true common dual face, including every primal variable bound.

    Bound rows allow free-variable stationarity equalities. The objective equality
    enforces strong duality; sequential fixes give a jointly feasible lexicographic
    vector, unlike combining independently calculated component minima.
    """
    if problem.sense != lp.LpMinimize:
        raise ValueError("Dual pricing expects a minimisation LP")
    variables = problem.variables()
    if any(v.cat != lp.LpContinuous for v in variables):
        raise ValueError("Freeze all discrete decisions before LP pricing")
    rows = []
    for name, c in problem.constraints.items():
        rows.append((name, dict(c.items()), -c.constant, c.sense))
    for var in variables:
        if var.lowBound is not None:
            rows.append((f"lower_{var.name}", {var: 1}, var.lowBound, 1))
        if var.upBound is not None:
            rows.append((f"upper_{var.name}", {var: 1}, var.upBound, -1))
    face = lp.LpProblem("optimal_dual_face", lp.LpMinimize)
    dual = {}
    for index, (name, _, _, sense) in enumerate(rows):
        dual[name] = lp.LpVariable(f"y_{index}", lowBound=0 if sense == 1 else None,
                                   upBound=0 if sense == -1 else None)
    # Build sparse columns once; materialising zero PuLP expressions for every
    # row/variable pair makes a full-day price-face construction needlessly dense.
    columns={v:[] for v in variables}
    for name,coefficients,_,_ in rows:
        for var,coefficient in coefficients.items():
            if coefficient:
                columns[var].append((name,coefficient))
    for j, var in enumerate(variables):
        face += (lp.lpSum(coefficient*dual[name] for name,coefficient in columns[var])
                 == problem.objective.get(var, 0)), f"stationarity_{j}"
    dual_objective = lp.lpSum(rhs*dual[name] for name, _, rhs, _ in rows)
    # CBC serialises primal values with limited decimal precision. Evaluating a
    # large welfare objective from those rounded values can move it off the exact
    # optimal face. Reconstruct the objective from row duals and bound reduced
    # costs, using the original (unrounded) RHS and bounds instead.
    optimum = lp_optimal_value(problem)
    face += dual_objective == optimum, "strong_duality"
    if extra:
        extra(face, dual)
    native = NativeFace(face) if highspy is not None else None
    bounds = {}
    for position,name in enumerate(names):
        if ranges:
            if native:
                low = native.optimize(dual[name])
                high = native.optimize(dual[name],True)
            else:
                face.sense = lp.LpMinimize
                face.setObjective(dual[name])
                low = solve(face, False)
                face.sense = lp.LpMaximize
                high = solve(face, False)
            bounds[name] = [low, high]
            if len(names)>100 and position%72==71:
                print(f"    Dual ranges: {position+1}/{len(names)}",flush=True)
    if not select:
        return {}, bounds
    face.sense = lp.LpMinimize
    result = {}
    for index, name in enumerate(names):
        if native:
            value = native.optimize(dual[name])
        else:
            face.setObjective(dual[name])
            value = solve(face, False)
        if abs(value) < 1e-7:
            value = 0.0
        result[name] = value
        if native:
            native.fix(dual[name],value)
        else:
            face += dual[name] == value, f"lex_fix_{index}"
        if len(names)>100 and index%72==71:
            print(f"    Joint price selection: {index+1}/{len(names)}",flush=True)
    if native:
        # Read one common feasible vector after all lexicographic restrictions;
        # do not combine values from different intermediate numerical bases.
        result={name:native.value(dual[name]) for name in names}
    return result, bounds


class NativeFace:
    """Warm-start one HiGHS dual-face LP for all ranges and lexicographic fixes."""
    def __init__(self,problem):
        variables=problem.variables()
        self.index={v:i for i,v in enumerate(variables)}
        rows=list(problem.constraints.values())
        matrix=[[] for _ in variables]
        rowlower=[];rowupper=[]
        inf=highspy.kHighsInf
        for i,c in enumerate(rows):
            rhs=-c.constant
            rowlower.append(rhs if c.sense>=0 else -inf)
            rowupper.append(rhs if c.sense<=0 else inf)
            for v,coefficient in c.items():
                matrix[self.index[v]].append((i,coefficient))
        model=highspy.HighsLp()
        model.num_col_=len(variables);model.num_row_=len(rows)
        model.col_cost_=[0.]*len(variables)
        model.col_lower_=[v.lowBound if v.lowBound is not None else -inf for v in variables]
        model.col_upper_=[v.upBound if v.upBound is not None else inf for v in variables]
        self.lower=list(model.col_lower_);self.upper=list(model.col_upper_)
        model.row_lower_=rowlower;model.row_upper_=rowupper
        start=[0];indices=[];values=[]
        for col in matrix:
            indices.extend(i for i,_ in col);values.extend(v for _,v in col)
            start.append(len(indices))
        model.a_matrix_.format_=highspy.MatrixFormat.kColwise
        model.a_matrix_.start_=start;model.a_matrix_.index_=indices;model.a_matrix_.value_=values
        self.solver=highspy.Highs()
        for k,v in {"output_flag":False,"threads":1,"time_limit":90.,
                    "primal_feasibility_tolerance":1e-8,"dual_feasibility_tolerance":1e-8}.items():
            self.solver.setOptionValue(k,v)
        self.solver.passModel(model)
        self.previous=None

    def optimize(self,variable,maximize=False):
        index=self.index[variable]
        if self.previous is not None:
            self.solver.changeColCost(self.previous,0.)
        self.solver.changeColCost(index,-1. if maximize else 1.)
        self.previous=index
        # HiGHS retains cumulative run time when this model is warm-started.
        # Give each range/selection solve its own 90-second budget, rather than
        # timing out the complete batch after 90 seconds of successful solves.
        self.solver.setOptionValue("time_limit",self.solver.getRunTime()+90.)
        self.solver.run()
        if self.solver.getModelStatus()!=highspy.HighsModelStatus.kOptimal:
            raise RuntimeError(f"Dual-face range failed: {self.solver.getModelStatus()}")
        return self.solver.getSolution().col_value[index]

    def fix(self,variable,value):
        index=self.index[variable]
        # Independent floating-point basis changes can differ in their last ulps.
        # Preserve a documented 1e-6 EUR/MW lexicographic tolerance, then return
        # the final jointly feasible vector rather than inconsistent exact fixes.
        self.solver.changeColBounds(index,max(self.lower[index],value-1e-6),
                                    min(self.upper[index],value+1e-6))

    def value(self,variable):
        value=self.solver.getSolution().col_value[self.index[variable]]
        return 0. if abs(value)<1e-8 else value


def perturbation(problem, name, price, epsilon=.001):
    row = problem.constraints[name]
    original = -row.constant
    base = lp.value(problem.objective)
    row.changeRHS(original+epsilon)
    derivative=None
    try:
        bumped = solve(problem, False)
        derivative = (bumped-base)/epsilon
    except RuntimeError:
        pass
    finally:
        row.changeRHS(original)
        solve(problem, False)
    return {"constraint": name, "epsilon_mw": epsilon, "price": price,
            "right_derivative": derivative,
            "status":"feasible" if derivative is not None else "infeasible_fixed_commitment",
            "matches": derivative is not None and abs(derivative-price) < .05}

"""Checked CBC solves and joint optimal-dual-face pricing for minimisation LPs."""
import pulp as lp
import math,time,tempfile
from pathlib import Path
from . import solver as control
try:
    import highspy
except ImportError:
    highspy = None

EPS = 1e-6


def solve(problem, mip=True):
    stage=control.stage_name(problem);options=control.options(stage)
    backend_name=options['backend']
    if backend_name=='auto':backend_name='highs' if highspy is not None else 'cbc'
    if backend_name=='highs' and highspy is None:raise ValueError('HiGHS backend requested but highspy is not installed')
    candidate=getattr(problem,'_emst_warm_start_values',None) if mip else None
    warm=getattr(problem,'_emst_warm_start',None) if mip else None
    class StartedHiGHS(lp.HiGHS):
        def callSolver(self,model):
            # PuLP can add a fixed dummy column for a constant objective after
            # candidate validation. Submit the known columns; let it fill that
            # harmless new column rather than indexing a missing seed value.
            variables=[v for v in model.variables() if v.name in candidate]
            try:
                status=model.solverModel.setSolution(len(variables),[v.index for v in variables],[candidate[v.name] for v in variables])
                warm['submission_status']=str(status)
            except RuntimeError as error:
                warm.update(submission_status='failed_cold_start',submission_error=str(error))
            super().callSolver(model)
    class StartedCBC(lp.PULP_CBC_CMD):
        def actualSolve(self,model,**kwargs):
            # CBC's command parser splits paths on spaces. Keep the MIP-start
            # files in an owned, relative, unique directory until it has read
            # them, then clean up. Do not share fixed filenames across solves.
            with tempfile.TemporaryDirectory(prefix='.emst-cbc-',dir='.') as folder:
                self._start_folder=Path(folder).name
                return super().actualSolve(model,**kwargs)
        def create_tmp_files(self,name,*extensions):
            return (str(Path(self._start_folder)/('model.'+ext)) for ext in extensions)
    backend_class=(StartedHiGHS if candidate else lp.HiGHS) if backend_name=='highs' else StartedCBC if candidate else lp.PULP_CBC_CMD
    extra={}
    if candidate and backend_name=='cbc':
        for v in problem.variables():v.varValue=candidate[v.name]
        extra.update(warmStart=True,keepFiles=True);warm['submission_status']='submitted_to_CBC_MIP_start'
    backend=backend_class(msg=options['logging'],mip=mip,gapRel=options['relative_gap'] if mip else 0,
        gapAbs=options['absolute_gap'] if mip else 0,threads=options['threads'],timeLimit=options['time_limit_seconds'],**extra)
    started=time.perf_counter();failure=None
    try:status=problem.solve(backend)
    except (lp.PulpSolverError,RuntimeError) as error:
        status=lp.LpStatusUndefined;failure=str(error)
    native=getattr(problem,'solverModel',None) if backend_name=='highs' else None
    native_status=str(native.getModelStatus()) if native is not None else None
    info=native.getInfo() if native is not None else None
    objective=lp.value(problem.objective)
    objective=float(objective) if objective is not None and math.isfinite(objective) else None
    optimal=(failure is None and status==lp.LpStatusOptimal and problem.sol_status==lp.LpSolutionOptimal)
    native_optimal=native_status is None or native_status.endswith('.kOptimal')
    reason='optimal' if optimal and native_optimal else 'unknown'
    if native_status and 'TimeLimit' in native_status:reason='time_limit'
    elif native_status and 'IterationLimit' in native_status:reason='iteration_limit'
    elif status==lp.LpStatusInfeasible:reason='infeasible'
    elif status==lp.LpStatusUnbounded:reason='unbounded'
    elif failure:reason='solver_error'
    elif optimal and not native_optimal:reason='solver_limit'
    elif not optimal:reason='not_optimal'
    def finite(value):return float(value) if value is not None and math.isfinite(value) else None
    bound=finite(getattr(info,'mip_dual_bound',None)) if mip else None
    if bound is not None:bound+=float(problem.objective.constant)
    incumbent=(bool(native.getSolution().value_valid) if native is not None else problem.sol_status in (lp.LpSolutionOptimal,lp.LpSolutionIntegerFeasible))
    record=dict(**control.details(),stage=stage,purpose=control.solve_purpose(mip),problem=problem.name,
        backend=backend_name,backend_version=highspy.Highs().version() if backend_name=='highs' else 'bundled_CBC',
        effective_options=options,status=lp.LpStatus.get(status,'Undefined'),native_status=native_status,
        solution_status=lp.LpSolution.get(problem.sol_status,'Unknown'),
        incumbent_available=incumbent,
        objective=objective if incumbent else None,best_bound=bound,
        gap=finite(getattr(info,'mip_gap',None)) if mip else None,
        wall_seconds=time.perf_counter()-started,termination_reason=reason,error=failure)
    if warm is not None:record['warm_start']=dict(warm)
    control.record(record)
    # Time-limited incumbents never opt in to settlement; configured gap criteria still require optimal termination.
    if reason!='optimal':raise control.SolveError(record)
    return objective or 0.


def primal_violation(variables,constraints):
    violation=0.
    for v in variables:
        value=v.varValue
        if value is None or not math.isfinite(value):return float('inf')
        if v.lowBound is not None:violation=max(violation,v.lowBound-value)
        if v.upBound is not None:violation=max(violation,value-v.upBound)
    for c in constraints:
        value=c.value()
        if value is None or not math.isfinite(value):return float('inf')
        violation=max(violation,abs(value) if c.sense==0 else -value if c.sense==1 else value)
    return violation


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
    native = NativeFace(face) if highspy is not None and control.options('diagnostic')['backend']!='cbc' else None
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
        self.options=control.options('diagnostic')
        self.solver=highspy.Highs()
        for k,v in {"output_flag":self.options["logging"],"threads":self.options["threads"],"time_limit":self.options["time_limit_seconds"],
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
        self.solver.setOptionValue("time_limit",self.solver.getRunTime()+self.options["time_limit_seconds"])
        started=time.perf_counter()
        self.solver.run()
        status=self.solver.getModelStatus()
        diagnostic_record=dict(**control.details(),stage='diagnostic',purpose='dual_face_range_or_selection',problem='optimal_dual_face',
            backend='highs',backend_version=self.solver.version(),effective_options=self.options,
            status=str(status),termination_reason='optimal' if status==highspy.HighsModelStatus.kOptimal else 'not_optimal',
            wall_seconds=time.perf_counter()-started)
        if 'TimeLimit' in str(status):diagnostic_record['termination_reason']='time_limit'
        control.record(diagnostic_record)
        if self.solver.getModelStatus()!=highspy.HighsModelStatus.kOptimal:
            raise control.SolveError(diagnostic_record)
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

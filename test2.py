# Full, self-contained script that:
#  - parses SymPy expressions to cvc5 terms (incl. exp/sin/cos/… and a UF LOG),
#  - proves ∀a,b. (conds(a,b) ⇒ ∃C>0. goal(a,b,C)) by refuting its negation,
#  - uses _parse() consistently inside quantified bodies via an environment,
#  - and ALSO supports providing a concrete witness for C via witness_C.

from sympy import sympify, Symbol
from sympy import Add, Mul, Pow, Le, Lt, Ge, Gt, exp, sin, cos, tan, asin, acos, atan, log
import cvc5
from cvc5 import Kind

# ---------- LOG UF ----------
_log_fun = None
def _get_LOG(s, R):
    global _log_fun
    if _log_fun is None:
        _log_fun = s.declareFun("LOG", [R], R)  # SMT-LIB: (declare-fun LOG (Real) Real)
    return _log_fun

# ---------- helpers ----------
def _prod(s, terms):
    if not terms:
        return s.mkReal("1")
    if len(terms) == 1:
        return terms[0]
    return s.mkTerm(Kind.MULT, *terms)

# ---------- parser ----------
def _parse(b, s, env=None, side_effects=True):
    """
    Parse a SymPy expression/string `b` to a cvc5 Term.
    - `env`: mapping from variable name -> bound VARIABLE Term (for quantifiers).
    - `side_effects`: when True, assert domain guards/axioms (e.g., for log).
                      Set to False inside quantified bodies.
    """
    env = {} if env is None else env
    R = s.getRealSort()
    e = sympify(b)

    # atoms
    if e.is_Symbol:
        name = str(e)
        if name in env:
            return env[name]  # use bound var if present
        return s.mkConst(R, name)
    if e.is_Number:
        return s.mkReal(str(e))

    # comparisons
    if e.func is Le:
        lhs, rhs = e.args
        return s.mkTerm(Kind.LEQ, _parse(lhs, s, env, side_effects),
                                _parse(rhs, s, env, side_effects))
    if e.func is Lt:
        lhs, rhs = e.args
        return s.mkTerm(Kind.LT,  _parse(lhs, s, env, side_effects),
                                _parse(rhs, s, env, side_effects))
    if e.func is Ge:
        lhs, rhs = e.args
        return s.mkTerm(Kind.GEQ, _parse(lhs, s, env, side_effects),
                                _parse(rhs, s, env, side_effects))
    if e.func is Gt:
        lhs, rhs = e.args
        return s.mkTerm(Kind.GT,  _parse(lhs, s, env, side_effects),
                                _parse(rhs, s, env, side_effects))

    # addition
    if e.func is Add:
        kids = [_parse(arg, s, env, side_effects) for arg in e.args]
        return s.mkTerm(Kind.ADD, *kids) if len(kids) > 1 else kids[0]

    # power
    if e.func is Pow:
        a, b = e.as_base_exp()
        return s.mkTerm(Kind.POW,
                        _parse(a, s, env, side_effects),
                        _parse(b, s, env, side_effects))

    # multiplication with explicit division extraction
    if e.func is Mul:
        numer_args, den_args = [], []
        for arg in e.args:
            if arg.func is Pow and arg.exp.is_number and arg.exp < 0:
                base_t = _parse(arg.base, s, env, side_effects)
                exp_pos = -arg.exp
                den_args.append(s.mkTerm(Kind.POW, base_t, s.mkReal(str(exp_pos))))
            else:
                numer_args.append(_parse(arg, s, env, side_effects))
        numer = _prod(s, numer_args)
        if den_args:
            denom = _prod(s, den_args)
            return s.mkTerm(Kind.DIVISION, numer, denom)
        return numer

    # transcendentals / trig
    if e.func is exp:
        return s.mkTerm(Kind.EXPONENTIAL, _parse(e.args[0], s, env, side_effects))
    if e.func is sin:
        return s.mkTerm(Kind.SINE, _parse(e.args[0], s, env, side_effects))
    if e.func is cos:
        return s.mkTerm(Kind.COSINE, _parse(e.args[0], s, env, side_effects))
    if e.func is tan:
        return s.mkTerm(Kind.TANGENT, _parse(e.args[0], s, env, side_effects))
    if e.func is asin:
        return s.mkTerm(Kind.ARCSINE, _parse(e.args[0], s, env, side_effects))
    if e.func is acos:
        return s.mkTerm(Kind.ARCCOSINE, _parse(e.args[0], s, env, side_effects))
    if e.func is atan:
        return s.mkTerm(Kind.ARCTANGENT, _parse(e.args[0], s, env, side_effects))

    # log as UF + (optional) local side conditions
    if e.func is log and len(e.args) == 1:
        xt  = _parse(e.args[0], s, env, side_effects)
        LOG = _get_LOG(s, R)
        app = s.mkTerm(Kind.APPLY_UF, LOG, xt)
        if side_effects:
            s.assertFormula(s.mkTerm(Kind.GT, xt, s.mkReal("0")))
            s.assertFormula(s.mkTerm(Kind.EQUAL, s.mkTerm(Kind.EXPONENTIAL, app), xt))
        return app

    raise NotImplementedError(f"Unsupported expression: {e}")

# ---------- prover ----------
def _prove(conditions, goal, existentials=("C",), witness_C=None):
    """
    Prove  ∀(universals). (AND(conditions) ⇒ ∃(existentials)>0 . goal)
    by refuting its negation. All parsing is routed through _parse.

    If `witness_C` is provided (e.g., "2" or 2), substitute C := witness_C
    in both conditions and goal and remove C from `existentials`, proving the
    stronger universally quantified statement with that concrete constant.
    """
    # ----- Optional: provide a witness for C -----
    if witness_C is not None:
        w = sympify(witness_C)
        if not w.is_number:
            return "not proved: witness_C must be numeric"
        if w.evalf() <= 0:
            return "not proved: witness_C must be > 0"
        # Substitute C := w in input strings before symbol collection
        conditions = [str(sympify(c).subs(Symbol("C"), w)) for c in conditions]
        goal       = str(sympify(goal).subs(Symbol("C"), w))
        existentials = tuple(n for n in existentials if n != "C")

    s = cvc5.Solver()
    s.setLogic("ALL")
    s.setOption("user-pat", "trust")   
    s.setOption("trigger-sel", "all")            # consider all potential triggers
    s.setOption("trigger-active-sel", "all") # honor user-specified triggers
    s.setOption("output", "inst")     # prints instantiations
    s.setOption("output", "trigger")  # prints selected triggers

    s.setOption("tlimit-per", "100000")      # ms per check
    s.setOption("rlimit-per", "200000")      # resource budget per check

    # new solver => reset global UF handle
    global _log_fun; _log_fun = None
    R = s.getRealSort()

    # --- extra axioms to connect order through exp/log (with patterns) ---
    LOG = _get_LOG(s, R)

    # quantifier variables used in axioms
    u = s.mkVar(R, "u")
    v = s.mkVar(R, "v")
    y = s.mkVar(R, "y")
    p = s.mkVar(R, "p")
    q = s.mkVar(R, "q")
    x = s.mkVar(R, "x")

    # Helper to attach a single trigger (pattern terms t1, t2, ...)
    def _forall_with_pattern(vars_list, body, *pattern_terms):
        pat  = s.mkTerm(Kind.INST_PATTERN, *pattern_terms)
        pats = s.mkTerm(Kind.INST_PATTERN_LIST, pat)
        return s.mkTerm(Kind.FORALL, s.mkTerm(Kind.VARIABLE_LIST, *vars_list), body, pats)

    # 1a) exp is monotone forward: (u <= v) -> exp(u) <= exp(v)
    s.assertFormula(_forall_with_pattern(
        [u, v],
        s.mkTerm(Kind.IMPLIES,
                 s.mkTerm(Kind.LEQ, u, v),
                 s.mkTerm(Kind.LEQ, s.mkTerm(Kind.EXPONENTIAL, u),
                                  s.mkTerm(Kind.EXPONENTIAL, v))),
        s.mkTerm(Kind.EXPONENTIAL, u), s.mkTerm(Kind.EXPONENTIAL, v)
    ))

    # 1b) exp is monotone reverse: (exp u <= exp v) -> (u <= v)
    s.assertFormula(_forall_with_pattern(
        [u, v],
        s.mkTerm(Kind.IMPLIES,
                 s.mkTerm(Kind.LEQ, s.mkTerm(Kind.EXPONENTIAL, u),
                                  s.mkTerm(Kind.EXPONENTIAL, v)),
                 s.mkTerm(Kind.LEQ, u, v)),
        s.mkTerm(Kind.LEQ, s.mkTerm(Kind.EXPONENTIAL, u),
                        s.mkTerm(Kind.EXPONENTIAL, v))
    ))

    # 2) Right inverse: LOG(exp y) = y   (trigger on LOG(exp y))
    s.assertFormula(_forall_with_pattern(
        [y],
        s.mkTerm(Kind.EQUAL, s.mkTerm(Kind.APPLY_UF, LOG, s.mkTerm(Kind.EXPONENTIAL, y)), y),
        s.mkTerm(Kind.APPLY_UF, LOG, s.mkTerm(Kind.EXPONENTIAL, y))
    ))

    # 3) Left inverse: x>0 -> exp(LOG x) = x   (trigger on exp(LOG x))
    s.assertFormula(_forall_with_pattern(
        [x],
        s.mkTerm(Kind.IMPLIES,
                 s.mkTerm(Kind.GT, x, s.mkReal("0")),
                 s.mkTerm(Kind.EQUAL,
                          s.mkTerm(Kind.EXPONENTIAL, s.mkTerm(Kind.APPLY_UF, LOG, x)), x)),
        s.mkTerm(Kind.EXPONENTIAL, s.mkTerm(Kind.APPLY_UF, LOG, x))
    ))

    # 4) log monotone on (0, ∞): p>0 ∧ q>0 ∧ p<=q -> LOG p <= LOG q
    s.assertFormula(_forall_with_pattern(
        [p, q],
        s.mkTerm(Kind.IMPLIES,
                 s.mkTerm(Kind.AND,
                          s.mkTerm(Kind.GT, p, s.mkReal("0")),
                          s.mkTerm(Kind.GT, q, s.mkReal("0")),
                          s.mkTerm(Kind.LEQ, p, q)),
                 s.mkTerm(Kind.LEQ,
                          s.mkTerm(Kind.APPLY_UF, LOG, p),
                          s.mkTerm(Kind.APPLY_UF, LOG, q))),
        s.mkTerm(Kind.APPLY_UF, LOG, p), s.mkTerm(Kind.APPLY_UF, LOG, q)
    ))

    # --------- build quantified proof obligation ---------
    # collect symbols from strings via SymPy
    cond_syms = set().union(*(sympify(c).free_symbols for c in conditions)) if conditions else set()
    goal_syms = sympify(goal).free_symbols
    all_names = {str(v) for v in (cond_syms | goal_syms)}

    # which names are existential? (default only "C")
    exi_names = [n for n in existentials if n in all_names]
    uni_names = sorted(all_names - set(exi_names))

    # build bound vars
    uni_vars = [s.mkVar(R, n) for n in uni_names]
    exi_vars = [s.mkVar(R, n) for n in exi_names]

    # environments for parsing under binders
    env_uni = dict(zip(uni_names, uni_vars))
    env_all = {**env_uni, **dict(zip(exi_names, exi_vars))}

    # parse conditions in universal env; no side effects inside binders
    if conditions:
        cond_terms = [_parse(c, s, env=env_uni, side_effects=False) for c in conditions]
        cond = cond_terms[0] if len(cond_terms) == 1 else s.mkTerm(Kind.AND, *cond_terms)
    else:
        cond = s.mkTrue()

    # parse goal in env that includes both universal and existential vars
    goal_term = _parse(goal, s, env=env_all, side_effects=False)

    # positivity constraints for existentials named "C" (if present)
    pos = []
    if "C" in env_all:
        pos.append(s.mkTerm(Kind.GT, env_all["C"], s.mkReal("0")))
    exists_body = goal_term if not pos else s.mkTerm(Kind.AND, *([*pos, goal_term]))

    # ∃-part
    exists = (s.mkTerm(Kind.EXISTS, s.mkTerm(Kind.VARIABLE_LIST, *exi_vars), exists_body)
              if exi_vars else goal_term)

    # ∀-part
    formula = (s.mkTerm(Kind.FORALL, s.mkTerm(Kind.VARIABLE_LIST, *uni_vars),
                        s.mkTerm(Kind.IMPLIES, cond, exists))
               if uni_vars else s.mkTerm(Kind.IMPLIES, cond, exists))

    # Prove validity: UNSAT(¬formula)
    s.assertFormula(s.mkTerm(Kind.NOT, formula))
    res = s.checkSat()
    if res.isUnknown():
        why = s.getInfo("reason-unknown")
        return f"unknown {why}"
    return "proved" if res.isUnsat() else "not proved"

# ---------- quick checks ----------
if __name__ == "__main__":
    # existential C (quantified search): may be harder on quantifiers but works on many inputs
    print(_prove(["a > 0", "b > 0", "exp(a) <= exp(b)"], "a <= C*b", witness_C=1))

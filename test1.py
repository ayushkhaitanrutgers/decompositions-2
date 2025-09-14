from sympy import *
import cvc5
from cvc5 import Kind
import itertools
_log_counter = itertools.count()

_log_fun = None

def _get_LOG(s, R):
    global _log_fun
    if _log_fun is None:
        _log_fun = s.declareFun("LOG", [R], R)  # SMT-LIB: (declare-fun LOG (Real) Real)
    return _log_fun




def _parse(b, s):
    R = s.getRealSort()
    e = sympify(b)

    if e.is_Symbol:
        return s.mkConst(R, str(e))
    if e.is_Number:
        return s.mkReal(str(e))
    
    if e.func==Le:
        lhs, rhs = e.args
        return s.mkTerm(Kind.LEQ, _parse(lhs,s), _parse(rhs, s))
    
    if sympify(b).func==Lt:
        lhs, rhs = e.args
        return s.mkTerm(Kind.LT, _parse(lhs,s), _parse(rhs, s))
    if sympify(b).func==Ge:
        lhs, rhs = e.args
        return s.mkTerm(Kind.GEQ, _parse(lhs,s), _parse(rhs, s))
    
    if sympify(b).func== Gt:
        lhs, rhs = e.args
        return s.mkTerm(Kind.GT, _parse(lhs,s), _parse(rhs, s))
    
    if e.func==Add:
        children = [_parse(arg, s) for arg in e.args]
        return s.mkTerm(Kind.ADD, *children)
    
    if e.func==Pow:
        a, b = e.as_base_exp()
        return s.mkTerm(Kind.POW, *[_parse(a,s), _parse(b,s)])
        
    
    if e.func is Mul:
        numer_args = []
        denom_args = []
        for arg in e.args:
            if arg.func is Pow and arg.exp.is_number and arg.exp < 0:
                denom_args.append(_parse(arg.base, s))
                exp_positive = -arg.exp
                denom_args[-1] = s.mkTerm(Kind.POW, denom_args[-1], s.mkReal(str(exp_positive)))
            else:
                numer_args.append(_parse(arg, s))
        numer = s.mkTerm(Kind.MULT, *numer_args) if len(numer_args) > 1 else numer_args[0]
        if denom_args:
            denom = s.mkTerm(Kind.MULT, *denom_args) if len(denom_args) > 1 else denom_args[0]
            return s.mkTerm(Kind.DIVISION, numer, denom)
        else:
            return numer

            

    
    if e.func == exp:
        return s.mkTerm(Kind.EXPONENTIAL, *[_parse(c,s) for c in e.args])
    
    if e.func == sin:
        return s.mkTerm(Kind.SINE, *[_parse(c,s) for c in e.args])
    
    if e.func == cos:
        return s.mkTerm(Kind.COSINE, *[_parse(c,s) for c in e.args])
    
    if e.func == tan:
        return s.mkTerm(Kind.TANGENT, *[_parse(c,s) for c in e.args])
    
    if e.func == asin:
        return s.mkTerm(Kind.ARCSINE, *[_parse(c,s) for c in e.args])
    
    if e.func == acos:
        return s.mkTerm(Kind.ARCCOSINE, *[_parse(c,s) for c in e.args])
    
    if e.func == atan:
        return s.mkTerm(Kind.ARCTANGENT, *[_parse(c,s) for c in e.args])
    
    # inside _parse(...)
    if e.func == log and len(e.args) == 1:
        xt = _parse(e.args[0], s)
        LOG = _get_LOG(s, R)
        app = s.mkTerm(Kind.APPLY_UF, LOG, xt)        # pretty-prints like (LOG <sexpr>)
        s.assertFormula(s.mkTerm(Kind.GT, xt, s.mkReal("0")))               # domain
        s.assertFormula(s.mkTerm(Kind.EQUAL, s.mkTerm(Kind.EXPONENTIAL, app), xt))  # exp(LOG x)=x
        return app
    
    
        
    raise NotImplementedError(f"Unsupported expression: {e}")



def _prove(conditions, goal):
    s = cvc5.Solver()
    s.setOption("produce-models", "true")
    s.setLogic("ALL")  # quantifiers allowed if they show up later
    # If you cached LOG globally, reset per-solver to avoid cross-solver Terms
    global _log_fun; _log_fun = None

    # assert premises
    for c in conditions:
        s.assertFormula(_parse(c, s))

    # existential symbols = symbols in goal but not in premises
    prem_syms = set().union(*(sympify(c).free_symbols for c in conditions)) if conditions else set()
    goal_syms = sympify(goal).free_symbols
    exist_syms = {str(v) for v in goal_syms - prem_syms}

    # encode "positive C" automatically if present
    if "C" in exist_syms:
        s.assertFormula(s.mkTerm(Kind.GT, _parse("C", s), s.mkReal("0")))

    # assert goal (this will also add log-domain guards via your parser)
    s.assertFormula(_parse(goal, s))

    r = s.checkSat()
    if r.isSat():
        return ("exists", {v: s.getValue(_parse(v, s)) for v in sorted(exist_syms)})
    return "does not exist"

print(_prove(["a <= b"], "a <= C*b"))    
    




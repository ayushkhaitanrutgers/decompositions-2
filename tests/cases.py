"""Edge cases for the robust prover.  Each case: (name, kwargs for prove_bigO, expected status,
extra checks).  Used by run_cases.py (plain script) and test_prover.py (pytest)."""

# expected: a status string, or a tuple of acceptable statuses
CASES = [
    # --- basics -------------------------------------------------------------
    ("constant needed", dict(lhs="2*x", rhs="x", variables="x", conditions="x>1"), "proved", {"constant_ge": 2}),
    ("x^2 vs x", dict(lhs="x^2", rhs="x", variables="x", conditions="x>1"), "disproved", {}),
    ("x^51 vs x", dict(lhs="x^51", rhs="x", variables="x", conditions="x>1"), "disproved", {}),
    ("x vs x^2", dict(lhs="x", rhs="x^2", variables="x", conditions="x>1"), "proved", {}),
    ("x^2 vs x on (0,1)", dict(lhs="x^2", rhs="x", variables="x", conditions="0<x<1"), "proved", {}),
    ("x vs x^2 on (0,1)", dict(lhs="x", rhs="x^2", variables="x", conditions="0<x<1"), "disproved", {}),
    ("no variables", dict(lhs="3", rhs="1", variables="", conditions=""), "proved", {"constant_ge": 3}),
    # --- classical inequalities --------------------------------------------
    ("AM-GM 3 vars", dict(lhs="(x*y*z)^(1/3)", rhs="(x+y+z)/3", variables="x,y,z", conditions="x>0, y>0, z>0"), "proved", {}),
    ("AM-GM 2 vars, unused var z", dict(lhs="(x*y)^(1/2)", rhs="(x+y)/2", variables="x,y,z", conditions="x>0, y>0"), "proved", {}),
    ("Young full domain", dict(lhs="x*y", rhs="x*Log[x]+Exp[y]", variables="{x,y}", conditions="{y>0, x>1}"), "proved", {}),
    ("Young y<=Log[x]", dict(lhs="x*y", rhs="x*Log[x]+Exp[y]", variables="x,y", conditions="y>0, x>1, y<=Log[x]"), "proved", {}),
    ("Young y>=Log[x]", dict(lhs="x*y", rhs="x*Log[x]+Exp[y]", variables="x,y", conditions="y>0, x>1, y>=Log[x]"), "proved", {}),
    ("xy vs x^2+y^2 (abs)", dict(lhs="x*y", rhs="x^2+y^2", variables="x,y", conditions="", absolute=True), "proved", {}),
    # --- logs, exps, roots --------------------------------------------------
    ("Log[x] vs Sqrt[x]", dict(lhs="Log[x]", rhs="Sqrt[x]", variables="x", conditions="x>1"), "proved", {}),
    ("Log[x] vs x^(1/10)", dict(lhs="Log[x]", rhs="x^(1/10)", variables="x", conditions="x>1"), "proved", {"constant_ge": 3.6}),
    ("Log[1+x] vs x", dict(lhs="Log[1+x]", rhs="x", variables="x", conditions="x>0"), "proved", {}),
    ("Exp[x] vs 1+x", dict(lhs="Exp[x]", rhs="1+x", variables="x", conditions="x>0"), "disproved", {}),
    ("Exp[-x] vs 1/(1+x)", dict(lhs="Exp[-x]", rhs="1/(1+x)", variables="x", conditions="x>0"), "proved", {}),
    ("1/x vs 1 near 0", dict(lhs="1/x", rhs="1", variables="x", conditions="x>0"), "disproved", {}),
    ("1/x vs 1 away from 0", dict(lhs="1/x", rhs="1", variables="x", conditions="x>=1"), "proved", {}),
    ("Sin[x] vs 1", dict(lhs="Sin[x]", rhs="1", variables="x", conditions=""), "proved", {}),
    # --- Max / Min / Abs ----------------------------------------------------
    ("Max vs sum", dict(lhs="Max[x,y]", rhs="x+y", variables="x,y", conditions="x>0, y>0"), "proved", {}),
    ("Abs difference", dict(lhs="Abs[x-y]", rhs="x+y", variables="x,y", conditions="x>0, y>0"), "proved", {}),
    ("one-sided, lhs can be negative", dict(lhs="x-y", rhs="x+y", variables="x,y", conditions="x>0, y>0"), "proved", {"warning_contains": "negative"}),
    ("two-sided with absolute", dict(lhs="x-y", rhs="x+y", variables="x,y", conditions="x>0, y>0", absolute=True), "proved", {}),
    # --- ill-posed inputs ---------------------------------------------------
    ("empty domain", dict(lhs="x", rhs="1", variables="x", conditions="x>1, x<0"), "ill-posed", {"message_contains": "empty"}),
    ("cube root of negative", dict(lhs="x^(1/3)", rhs="x+2", variables="x", conditions="x>-1"), "ill-posed", {"message_contains": "real"}),
    ("rhs negative", dict(lhs="x", rhs="-x", variables="x", conditions="x>0"), "ill-posed", {"message_contains": "negative"}),
    ("unlisted symbol", dict(lhs="a*x", rhs="x", variables="x", conditions="x>0"), "ill-posed", {"message_contains": "a"}),
    ("euler e written as e", dict(lhs="e^x", rhs="Exp[x]", variables="x", conditions="x>0"), "ill-posed", {"message_contains": "Euler"}),
    ("unknown function", dict(lhs="foo(x)", rhs="x", variables="x", conditions="x>0"), "error", {"message_contains": "foo"}),
    ("syntax error", dict(lhs="x +* 2", rhs="x", variables="x", conditions="x>0"), "error", {"message_contains": "parse"}),
    ("inequality as lhs", dict(lhs="x < 2", rhs="x", variables="x", conditions="x>0"), "error", {}),
    # --- input in other syntaxes ---------------------------------------------
    ("python syntax", dict(lhs="x**2 + log(x)", rhs="x**2", variables="x", conditions="x>1"), "proved", {}),
    ("reserved variable name E", dict(lhs="E*x", rhs="x*E**2", variables="E, x", conditions="E>1, x>0"), "proved", {"warning_contains": "renamed"}),
    ("conditions with &&", dict(lhs="x", rhs="x^2", variables="x", conditions="x>1 && x<100"), "proved", {}),
    ("unicode <=", dict(lhs="x", rhs="x^2", variables="x", conditions="x ≥ 1"), "proved", {}),
    # --- probably false but hard ---------------------------------------------
    ("hard false", dict(lhs="Exp[x*y]", rhs="(Exp[x]+Exp[y])^3", variables="x,y", conditions="x>0, y>0"), "disproved", {}),
]

COVERAGE_CASES = [
    ("two halves", dict(variables="x", conditions="x>0", subdomains=["x<=1", "x>=1"]), "yes", {}),
    ("gap at 1", dict(variables="x", conditions="x>0", subdomains=["x<1", "x>1"]), "no", {}),
    ("empty piece", dict(variables="x", conditions="x>0", subdomains=["x<=1", "x>=1", "x>5 && x<3"]), "yes", {"empty": [3]}),
    ("Young split", dict(variables="x,y", conditions="x>1, y>0", subdomains=["y<=Log[x]", "y>=Log[x]"]), "yes", {}),
    ("Young split with base repeated", dict(variables="x,y", conditions="x>1, y>0", subdomains=["x>1 && y>0 && y<=Log[x]", "x>1 && y>0 && y>=Log[x]"]), "yes", {}),
    ("two-variable gap", dict(variables="x,y", conditions="x>0, y>0", subdomains=["x>y", "x<y"]), "no", {}),
]

# Constants that may depend on parameters (prove_bigO(..., parameters=...)).
PARAM_CASES = [
    ("param: Log[x] vs x^eps", dict(lhs="Log[x]", rhs="x^eps", variables="x", conditions="x>=1, eps>0, eps<1", parameters="eps"),
     "proved", {"constant_contains": "eps"}),
    ("param: uniform anyway", dict(lhs="x", rhs="x+a", variables="x", conditions="x>0, a>0", parameters="a"),
     "proved", {"message_contains": "does not depend"}),
    ("param: false for every k", dict(lhs="Exp[x]", rhs="x^k", variables="x", conditions="x>1, k>1", parameters="k"),
     "disproved", {}),
    ("param: polynomial, symbolic C", dict(lhs="a*x", rhs="x", variables="x", conditions="x>0, a>0", parameters="a"),
     "proved", {"constant_contains": "a"}),
    ("param: same symbol twice", dict(lhs="x", rhs="x", variables="x", conditions="x>0", parameters="x"),
     "error", {}),
]

# "Eventually" (the estimate only has to hold for large values of some variables)
# and integer variables.
PARAM_CASES += [
    ("eventually: 1/x vs 1", dict(lhs="1/x", rhs="1", variables="x", conditions="x>0", eventually="x"),
     "proved", {"message_contains": "x >= 1"}),
    ("eventually: x^2 vs x false", dict(lhs="x^2", rhs="x", variables="x", conditions="x>0", eventually="x"),
     "disproved", {"message_contains": "arbitrarily large"}),
    ("eventually: two vars false", dict(lhs="x*y", rhs="x^2+y", variables="x,y", conditions="x>0, y>0", eventually="x"),
     "disproved", {}),
    ("integer var proved", dict(lhs="Log[n]", rhs="n^(1/3)", variables="n", conditions="Element[n, Integers], n>=1"),
     "proved", {"warning_contains": "relaxed"}),
    ("integer var not disproved", dict(lhs="x^2", rhs="x", variables="x", conditions="Element[x, Integers], x>0"),
     "unknown", {"message_contains": "integer"}),
    ("eventually: unlisted symbol", dict(lhs="x", rhs="x", variables="x", conditions="x>0", eventually="y"),
     "error", {}),
]

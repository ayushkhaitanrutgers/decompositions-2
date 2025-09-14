from sympy import sympify, Le, Ge, Lt, Gt, Eq, Symbol, summation, oo
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, DefaultDict
from dataclasses import dataclass
from axioms import transitivity, le_to_bigo, ge_to_bigo, lt_to_bigo, gt_to_bigo
from shared import Term

from z3_experiments import *

# pip install cvc5
import cvc5
from cvc5 import Kind

#given the splits from the LLM, we want to confirm that the inequality for a certain function is true
def prove():
    slv = cvc5.Solver()
    slv.setLogic("ALL")   # quantifiers + nonlinear reals

    R = slv.getRealSort()

    # --- bound variables for the quantifiers ---
    a = slv.mkVar(R, "a")
    b = slv.mkVar(R, "b")
    C = slv.mkVar(R, "C")   # must be mkVar (bound), not mkConst

    # Useful constants/terms
    zero = slv.mkReal(0)
    two  = slv.mkReal(2)
    avg  = slv.mkTerm(Kind.DIVISION, slv.mkTerm(Kind.ADD, a, b), two)
    ab   = slv.mkTerm(Kind.MULT, a, b)
    sqrt_ab = slv.mkTerm(Kind.SQRT, ab)   # true sqrt operator (not x**(1/2))

    # Premises: a >= 0 ∧ b >= 0 ∧ a <= b
    prem = slv.mkTerm(
        Kind.AND,
        slv.mkTerm(Kind.GEQ, a, zero),
        slv.mkTerm(Kind.GEQ, b, zero),
        slv.mkTerm(Kind.LEQ, a, b),
    )

    # Exists C: (C > 0) ∧ (avg >= C * sqrt(ab))
    exists_body = slv.mkTerm(
        Kind.AND,
        slv.mkTerm(Kind.GT, C, zero),
        slv.mkTerm(Kind.GEQ, avg, slv.mkTerm(Kind.MULT, C, sqrt_ab)),
    )

    exists_C = slv.mkTerm(Kind.EXISTS, slv.mkTerm(Kind.VARIABLE_LIST, C), exists_body)

    # ForAll a,b: prem ⇒ Exists C ...
    forall_ab = slv.mkTerm(
        Kind.FORALL,
        slv.mkTerm(Kind.VARIABLE_LIST, a, b),
        slv.mkTerm(Kind.IMPLIES, prem, exists_C),
    )

    # Prove by refuting the negation
    slv.assertFormula(slv.mkTerm(Kind.NOT, forall_ab))
    return slv.checkSat()  # expect: unsat

print(prove())



def _series_split_calculate(kwargs):
    arr=[]
    for i in range(len(kwargs)):
        if i==0:
            arr.append(summation(sympify("a**n"),(sympify("n"),-oo,kwargs[i])))
        elif i==len(kwargs)-1:
            arr.append(summation(sympify("a**n"),(sympify("n"),kwargs[i]+1,oo)))
        else:
            arr.append(summation(sympify("a**n"),(sympify("n"),kwargs[i]+1,kwargs[i+1])))
    return arr
            

            
        



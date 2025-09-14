from sympy import sympify
from shared import Term
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, DefaultDict

def transitivity(arr: List[Term]):
    if len(arr)==2:
        a, b = arr[0], arr[1]
        if a.rel == b.rel:
            if a.rhs == b.lhs:
                return Term(rel = a.rel, lhs = a.lhs, rhs = b.rhs)
        
def le_to_bigo(a: Term):
    if a.rel.lower()=="le":
        return Term(rel = "BigO", lhs= a.lhs, rhs = a.rhs)
    
def lt_to_bigo(a : Term):
    if a.rel.lower()=="lt":
        return Term(rel = "BigO", lhs= a.lhs, rhs = a.rhs)
    
def ge_to_bigo(a: Term):
    if a.rel.lower()=="ge":
        return Term(rel = "BigO", lhs= a.rhs, rhs = a.lhs)
    
def gt_to_bigo(a : Term):
    if a.rel.lower()=="gt":
        return Term(rel = "BigO", lhs= a.rhs, rhs = a.lhs)



    
    
    
    
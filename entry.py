from sympy import sympify, Le, Ge, Lt, Gt, Eq, Symbol, summation, symbols, oo
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, DefaultDict
from dataclasses import dataclass
from axioms import transitivity, le_to_bigo, ge_to_bigo, lt_to_bigo, gt_to_bigo
from shared import Term

#We define the class term below
        
#We will assume that a problem has to be inserted in a particular way. 
#We don't have to assume that the goal has a question mark up front. 
@dataclass
class Problem:
    conditions: List[str]
    given: List[str]
    goal: str

    

#We now define the axioms, that we will use to convert one tuple of things to another.
#This will be a series of functions
axioms = { "Transitivity" : transitivity, 
          "le_to_bigo" : le_to_bigo, 
          "ge_to_bigo": ge_to_bigo,
          "gt_to_bigo": gt_to_bigo,
          "lt_to_bigo": lt_to_bigo
          
    
}

given = ["BigO(m,n)", "BigO(n,p)"]
goal = "?BigO(m,p)"

#Let us now create a function that can create terms out of given
def _parse_given(given):
    arr = []
    for p in given:
        a = re.search(r"(\w+)\(([A-Za-z0-9]+)\,([A-Za-z0-9]+)\)", p)
        arr.append(Term(rel = a.group(1), lhs = a.group(2), rhs = a.group(3)))
    return arr

#We also create a function that can parse the goal
def _parse_goal(goal):
    a = re.search(r"(\w+)\(([A-Za-z0-9]+)\,([A-Za-z0-9]+)\)", goal)
    return Term(rel = a.group(1), lhs = a.group(2), rhs = a.group(3))

#We now also have to parse the conditions
def _parse_conditions(conditions: List[str]):
    pass
    
# We also need a way to check if two Terms are the same

def _eq(a: Term, b: Term):
    if a.rel == b.rel:
        if a.lhs == b.lhs:
            if a.rhs == b.rhs:
                return True
    return False
    

# We now parse the given and the goal. We create Terms out of them. 
given = _parse_given(given)
goal = _parse_goal(goal)

#Alright. So we are on the right track. Let us now try to do this without an LLM. 
#We want to make sure that we can carry out the proof of the AM-GM thing. 
#Let's do it for two functions. (a+b)/2 \geq C* \sqrt{a*b}

a = Term(rel = "BigO", lhs = "(a*b)^(1/2)", rhs = "(a+b)/2")
print(a.rhs)

#so what do we need? we need to divide the domain, and then check that this is true. 


for c in range(-2,2):
    print(c)


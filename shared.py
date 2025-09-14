# requirements: sympy, cvc5
from dataclasses import dataclass
from typing import List, Dict, Iterable, Optional, Union, Any
import sympy as sp
from sympy.parsing.sympy_parser import parse_expr
from sympy import symbols

import cvc5
from cvc5 import Kind

@dataclass
class Term:
    rel: str
    lhs : Any
    rhs : Any    
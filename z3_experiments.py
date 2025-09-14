import sympy as sp
import cvc5
from cvc5 import Kind
from sympy import sympify



class CVC5Encoder:
    def __init__(self):
        self.slv = cvc5.Solver()
        # Enable transcendentals: pick ALL or an arithmetic+T logic like QF_NRAT
        self.slv.setLogic("ALL")   # or "ALL"
        self.R = self.slv.getRealSort()
        self.vars = {}
        self.side_constraints = []
        self._gensym = 0

    def _const(self, name):
        if name not in self.vars:
            self.vars[name] = self.slv.mkConst(self.R, name)   # free constant
        return self.vars[name]

    def encode(self, expr):
        expr = sp.sympify(expr)

        if expr.is_Symbol:
            return self._const(str(expr))

        if expr.is_Number:
            return self.slv.mkReal(str(expr))

        if expr.is_Add:
            return self.slv.mkTerm(Kind.ADD, *[self.encode(a) for a in expr.args])

        if expr.is_Mul:
            return self.slv.mkTerm(Kind.MULT, *[self.encode(a) for a in expr.args])

        if expr.is_Pow:
            b, e = expr.as_base_exp()
            return self.slv.mkTerm(Kind.POW, self.encode(b), self.encode(e))

        # exp(x)
        if expr.func == sp.exp:
            return self.slv.mkTerm(Kind.EXPONENTIAL, self.encode(expr.args[0]))

        # log(x)  ==> introduce y with exp(y)=x  and x>0, return y
        if expr.func == sp.log:
            x_term = self.encode(expr.args[0])
            y = self._const(f"_log_{self._gensym}"); self._gensym += 1
            self.side_constraints.append(
                self.slv.mkTerm(Kind.EQUAL, self.slv.mkTerm(Kind.EXPONENTIAL, y), x_term)
            )
            zero = self.slv.mkReal(0)
            self.side_constraints.append(self.slv.mkTerm(Kind.GT, x_term, zero))
            return y

        # relations
        from sympy.core.relational import Relational  # explicit import avoids namespace issues
        if isinstance(expr, Relational):
            lhs, rhs = self.encode(expr.lhs), self.encode(expr.rhs)
            op = {
                "==": Kind.EQUAL, ">=": Kind.GEQ, "<=": Kind.LEQ, ">": Kind.GT, "<": Kind.LT
            }[expr.rel_op]
            return self.slv.mkTerm(op, lhs, rhs)

        raise NotImplementedError(f"Don't know how to translate {expr}")

# --- example ---
# enc = CVC5Encoder()
# t = enc.encode(sp.sympify("log(a) >= 1"))
# for c in enc.side_constraints:
#     enc.slv.assertFormula(c)
# enc.slv.assertFormula(t)  # the inequality itself
# print(enc.slv.checkSat())  # expects SAT/UNSAT/UNKNOWN

i = 0
while True:
    print(i)
    i+=1
    if i==2:
        break

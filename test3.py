# Prove: exp(a) <= exp(b)  =>  a <= C*b  with witness C = 1 (so a <= b)
# We assert: exp(a) <= exp(b), a > b, and the ground lemma (a > b) -> exp(a) > exp(b),
# then obtain an UNSAT proof.

import cvc5
from cvc5 import Kind, ProofFormat, ProofComponent

s = cvc5.Solver()
s.setLogic("QF_ALL")
s.setOption("produce-proofs", "true")
# Pick a proof format (newer builds use 'proof-format', older use 'proof-format-mode')
try:
    s.setOption("proof-format", "cpc")
except RuntimeError:
    s.setOption("proof-format-mode", "cpc")

R = s.getRealSort()
a = s.mkConst(R, "a")
b = s.mkConst(R, "b")
exp_a = s.mkTerm(Kind.EXPONENTIAL, a)
exp_b = s.mkTerm(Kind.EXPONENTIAL, b)

# Premise and negated goal (C=1)
s.assertFormula(s.mkTerm(Kind.LEQ, exp_a, exp_b))
s.assertFormula(s.mkTerm(Kind.GT, a, b))

# Ground monotonicity instance (strictly increasing exp on reals)
s.assertFormula(
    s.mkTerm(Kind.IMPLIES,
             s.mkTerm(Kind.GT, a, b),
             s.mkTerm(Kind.GT, exp_a, exp_b))
)

res = s.checkSat()
print("result:", res)        # should be: unsat
if res.isUnsat():
    # Handle API differences across cvc5 versions:
    # - Some return a Proof from getProof()
    # - Some return a sequence of Proofs from getProof() or getProof(ProofComponent.FULL)
    def _first_proof(maybe_seq):
        try:
            # If it's a sequence-like (list/tuple or has __len__/__getitem__), take first
            if isinstance(maybe_seq, (list, tuple)):
                return maybe_seq[0] if maybe_seq else None
            # generic sequence (e.g., cvc5 vector) detection
            if hasattr(maybe_seq, "__len__") and hasattr(maybe_seq, "__getitem__"):
                return maybe_seq[0] if len(maybe_seq) > 0 else None
        except Exception:
            pass
        return maybe_seq

    prf_obj = None
    try:
        # Preferred simple API if available
        prf_obj = _first_proof(s.getProof())
    except Exception:
        # Fallback to component-based API
        prf_obj = _first_proof(s.getProof(ProofComponent.FULL))

    if prf_obj is None:
        print("[warn] No proof object available to print.")
    else:
        try:
            print(s.proofToString(prf_obj, ProofFormat.DEFAULT))
        except TypeError:
            # Fallback: try default formatting without explicit format if supported
            try:
                print(s.proofToString(prf_obj))
            except Exception as e:
                print(f"[warn] Unable to print proof: {e}")

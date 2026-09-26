# Decompositions Project

This project answers a question posed by Terry Tao [here](https://terrytao.wordpress.com/2025/05/01/a-proof-of-concept-tool-to-verify-estimates/#n2) and [here](https://mathoverflow.net/questions/463937/what-mathematical-problems-can-be-attacked-using-deepminds-recent-mathematical/463940#463940). 

>[!NOTE]
>Given two functions $f$ and $g$ defined on a domain $\mathcal{D}$, the notation $f\ll g$ implies that there exists a positive constant $C>0$ such that $$f \leq C*g$$ at every point in $\mathcal{D}$. Assuming that the functions don't blow up at any point in $D$, this is equivalent to $f = O(g)$. 

Inspired by Google DeepMind's [alphageometry](https://github.com/google-deepmind/alphageometry), this repository explores using an LLM to propose natural subdomain decompositions where proving these estimates is much simpler, and then using a Computer Algebra System to verify that these estimates are indeed true in each of these subdomains. 

## What It Does
- Prompts an LLM to propose a minimal set of “natural” subdomains where a target asymptotic should be trivial to prove.
- Verifies each proposed subdomain by querying a CAS (Mathematica via `wolframscript`) to certify the estimate (a Big-O–style bound). Note that we only use the Resolve[] function is used in Mathematica, which returns True only if a statement is fully verified through a series of logical steps. Hence, this provides a fully rigorous proof of the estimate, and not merely numerical support. 
- Reports whether the inequality is proved on the full domain based on the verified subdomains.

Note that for alphageometry, an LLM was trained from scratch to be able to solve classical plane geometry problems. However, in our case, we merely leverage the highly impressive mathematical capabilities of the leading LLMs, and then use Computer Algebra Systems to provide a proof certificate. Note that we do not use SMT solvers to verify these esimates, because SMT solvers are not great at dealing with transcendental functions. Although CVC5 is reasonably good, we overall found Mathematica to be much more reliable for proofs involving log, exp, etc. 

## Prerequisites
- Python 3.9+
- An LLM API key
  - Put your key in a local `.env` file. We use Gemini-2.5-Flash for our demonstrations, and hence our API key is stored as `GOOGLE_API_KEY=<your_key>` or `GEMINI_API_KEY=<your_key>`.
  - The code auto-loads `.env` (via `python-dotenv` if present) or parses it directly.
- Computer Algebra System
  - Mathematica installed with `wolframscript` available on your PATH.
    - If it’s not on PATH, set `WOLFRAMSCRIPT=/path/to/wolframscript` in your environment.
  - Note: You can adapt the CAS layer to SageMath, but the current code path uses Mathematica. (See `mathematica_export.py`.)

## Setup
```bash
pip install -r requirements.txt
echo "GOOGLE_API_KEY=your_api_key_here" > .env  # or GEMINI_API_KEY
```

If `wolframscript` is not auto-detected, set its location:
```bash
export WOLFRAMSCRIPT=/usr/local/bin/wolframscript  # example
```

## Run
```bash
python mathematica_export.py
```

## The robust prover (`prover.py` + `prover.wl`)

All Mathematica work now goes through `prover.py`, which drives the kernel
script `prover.wl`.  It answers one question:

> is there a constant `C > 0` with `lhs <= C * rhs` at every point of the domain?

and returns one of `proved` (with an explicit `C`), `disproved` (with a reason),
`ill-posed` (the question itself has a problem), `error` (bad input) or `unknown`.

```bash
python prover.py "x*y" "x*Log[x] + Exp[y]" "x, y" "x > 1, y > 0"
python prover.py "Log[x]" "x^eps" "x" "x >= 1, eps > 0, eps < 1" --parameters eps   # C may depend on eps
python prover.py "1/x" "1" "x" "x > 0" --eventually x                                # only for x large
python prover.py "x^2" "x" "x" "x > 1" --json
```

```python
from prover import prove_bigO
r = prove_bigO("(x*y*z)^(1/3)", "(x+y+z)/3", "x, y, z", "x>0, y>0, z>0")
r.proved, r.constant, r.message
```

What it does that the old single `Resolve[Implies[...]]` call did not:

- **Constant ladder.** Tries `C = 1, 2, 4, 10, 100, 1000, 10^6`, then solves for
  the best constant symbolically (`Reduce` in `C`) and via the exact maximum of
  `lhs/rhs` (`MaxValue`).  The old code tried only `C = 1` and reported "This is
  False" when `C = 1` failed, which is wrong whenever a bigger constant works.
- **Real disproofs.** "Disproved" is printed only when Mathematica proves that no
  constant works: the ratio `lhs/rhs` is unbounded, or `rhs = 0` somewhere with
  `lhs > 0`, or the ratio tends to infinity along an explicit path, or `Resolve`
  proves "for every C there is a bad point".  Otherwise a counterexample for the
  largest constant tried is reported and the status is `unknown`.
- **Several methods and rewrites.** `Resolve`, `Reduce`, `Simplify`,
  `FullSimplify`, each on the original statement and on equivalent rewrites
  (`x -> E^u` when logs are present, `x -> t^q` for roots, shifting `y >= g(x)`
  to `y = g(x) + s`, expanding `Max`/`Min`/`Abs`).  Young's inequality
  `x*y << x*Log[x] + Exp[y]` is now proved directly on the whole domain in about
  40 s; before, it needed an LLM decomposition.
- **Sanity checks first.** Empty domain, symbols that are not declared as
  variables (`e` for Euler's number, `pi`, a stray `a`), functions Mathematica
  does not know (`foo(x)`), expressions that are not real on the domain
  (`x^(1/3)` for `x < 0`), a right-hand side that is negative somewhere (then
  `lhs << rhs` makes no sense; use `absolute=True` for `|lhs| <= C |rhs|`), a
  left-hand side that can be negative (a warning: the proof is one-sided), and
  `rhs = 0` points.
- **Input cleaning.** Accepts `x**2`, `log(x)`, `sqrt(x)`, `≤`, `≥`, conditions
  separated by commas or `&&`, and renames variables that clash with Mathematica
  (`C`, `D`, `E`, `I`, `N`, `K`, `O`...).
- **Constants depending on parameters.** With `parameters="eps"` the prover looks
  for `C = f(eps)`: `Log[x] <= (1/eps) x^eps` for `x >= 1`, `0 < eps < 1`.
- **"For x large enough."** With `eventually="x"` the estimate only has to hold
  for `x >= T` for some threshold `T` (thresholds 1, 10, ..., 10^6 are tried),
  which is what `f(x) << g(x)` as `x -> Infinity` means.  A disproof then has
  to show that `lhs/rhs` blows up for arbitrarily large `x` (along a path, or
  after maximising over the other variables), not merely somewhere.
- **Integer variables.** `Element[n, Integers]` is relaxed to real `n`: a proof
  for real `n` covers the integers, and a failure for real `n` is reported as
  `unknown`, not as a disproof.
- **Time limits that hold.** Every Mathematica call has a time limit.  Since
  `TimeConstrained` does not always interrupt the kernel, a watchdog in Python
  kills a stuck kernel, restarts it, and replays the finished steps from a log,
  so one hard sub-problem never blocks the rest.  No stray kernels are left
  behind.
- **Coverage check for decompositions.** When the LLM proposes subdomains,
  `check_coverage` makes Mathematica confirm that their union is the whole
  domain.  "Proved everywhere" is printed only if that check passes; before,
  a proof on pieces that missed part of the domain was reported as a full proof.

Tests: `python tests/run_cases.py` (about 50 inequalities with expected
answers, ~5 min) and `pytest tests/` (unit tests plus a watchdog test).

On Princeton's Della cluster: `module load mathematica/14.2.0` puts the kernel
on the PATH; otherwise set `WOLFRAM_KERNEL=/path/to/wolfram` (fast, ~2 s
start-up) or `WOLFRAMSCRIPT=/path/to/wolframscript`.

## CLI
You can now add the questions you want to prove in the examples.py file, and then attempt to prove them by running
```bash
decomp prove inequality_<inequality number here>
```
or 
```bash
decomp series series_<series number here>
```

This invokes the flow that queries the LLM for subdomains and verifies them with Mathematica. The script prints a status such as `It is proved` when the CAS verifies the inequality under the proposed decomposition.



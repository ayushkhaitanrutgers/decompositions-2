# Decompositions Project

This is joint work with [Dr. Vijay Ganesh](https://www.cc.gatech.edu/people/vijay-ganesh). 

This project fully solves a question asked by Terry Tao [here](https://terrytao.wordpress.com/2025/05/01/a-proof-of-concept-tool-to-verify-estimates/#n2) and [here](https://mathoverflow.net/questions/463937/what-mathematical-problems-can-be-attacked-using-deepminds-recent-mathematical/463940#463940). 

>[!NOTE]
>Given two functions $f$ and $g$ defined on a domain $\mathcal{D}$, the notation $f\ll g$ implies that there exists a positive constant $C>0$ such that $$f \leq >C*g$$ at every point in $\mathcal{D}$. Assuming that the functions don't blow up at any point in $D$, this is equivalent to $f = O(g)$. 

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

## CLI
You can now add the questions you want to prove in the examples.py file, and then attempt to prove them by running
```bash
decomp prove question_<question number here>
```
or 
```
decomp series series_<series number here>
```

This invokes the flow that queries the LLM for subdomains and verifies them with Mathematica. The script prints a status such as `It is proved` when the CAS verifies the inequality under the proposed decomposition.


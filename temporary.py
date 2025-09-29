import subprocess, shlex, os, shutil, json
from typing import Any, List
from llm_client import api_call, api_call_series
from dataclasses import dataclass
import re

def _resolve_wolframscript() -> str:
    # Prefer explicit env override
    env_path = os.environ.get("WOLFRAMSCRIPT")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    # Try PATH
    which_path = shutil.which("wolframscript")
    if which_path:
        return which_path

    # Common install locations (include default Wolfram.app path on macOS)
    for p in (
        "/Users/ayushkhaitan/Desktop/Wolfram.app/Contents/MacOS/wolframscript",
        "/Applications/Wolfram.app/Contents/MacOS/wolframscript",
        "/usr/local/bin/wolframscript",
        "/opt/homebrew/bin/wolframscript",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    raise FileNotFoundError(
        "wolframscript not found. Set $WOLFRAMSCRIPT or ensure it's on PATH."
    )

WOLFRAMSCRIPT = _resolve_wolframscript()

def _clean_env() -> dict:
    # strip DYLD* to avoid collisions; preserve PATH
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD")}
    env["PATH"] = os.environ.get("PATH", "")
    return env

def wl_eval(expr: str, form: str = "InputForm") -> str:
    """Evaluate Wolfram Language `expr` and return string in `form`.

    - `form` examples: "InputForm", "FullForm", "OutputForm".
    - Returns the exact textual rendering from wolframscript.
    """
    env = _clean_env()
    wrapped = f'ToString[({expr}), {form}]'
    cmd = [WOLFRAMSCRIPT, "-code", wrapped]
    return subprocess.check_output(cmd, text=True, env=env).strip()

def wl_eval_json(expr: str):
    """Evaluate `expr` and parse result via Wolfram's JSON export.

    Uses ExportString[..., "JSON"] on the Wolfram side, then json.loads.
    Not all symbolic results are JSON-serializable; in that case this raises.
    """
    env = _clean_env()
    wrapped = f'ExportString[({expr}), "JSON"]'
    cmd = [WOLFRAMSCRIPT, "-code", wrapped]
    data = subprocess.check_output(cmd, text=True, env=env).strip()
    return json.loads(data)

def wl_bool(expr: str) -> bool:
    out = wl_eval(expr, form="InputForm")
    if out == "True": return True
    if out == "False": return False
    raise ValueError(f"Unexpected output: {out!r}")

#The following is to separate the executables
def attempt_proof(vars, conds, lhs, rhs):
    def _normalize_wl(s: str) -> str:
        return (s.replace('exp[','Exp[')
                 .replace('log[','Log[')
                 .replace('ln[','Log['))

    lhs_wl = _normalize_wl(lhs)
    rhs_wl = _normalize_wl(rhs)

    vars_text = vars.strip()
    if vars_text.startswith('{') and vars_text.endswith('}'):
        vars_text = vars_text[1:-1]
    vars_code = '{' + vars_text + '}' if vars_text else '{}'

    conds_code = conds.strip()
    if not (conds_code.startswith('{') and conds_code.endswith('}')):
        conds_code = '{' + conds_code + '}' if conds_code else '{}'
    conds_code = _normalize_wl(conds_code)

    # Try a range of constants (C = 10^c)
    for c in range(-2, 7):
        a = wl_eval(f"""
witnessBigO[vars_, conds_, lhs_, rhs_, c_] :=
  Module[{{S}},
    S = If[conds === {{}}, True, And @@ conds];
    Resolve[ForAll[vars, Implies[S, lhs <= 10^c*rhs]], Reals]
  ];
witnessBigO[{vars_code}, {conds_code}, {lhs_wl}, {rhs_wl}, {c}]
        """)
        if a == 'True':  return f'It is proved with C=10^{c}'
        if a == 'False': return 'This is False'
    return 'Status unknown. Try a different setup'

        

# prompt = """I want to prove that in the domain x>0 and y>1, we have that x*y <= y*log[y]+Exp[x].
# This proof becomes trivial if the domain is decomposed into the right subdomains. Find these correct decompositions for me.
# Just give me the description of the subdomains in the form of an array, where each element of the array describes some subdomain. 
# Be very careful. You're the best at mathematics. You don't make mistakes in such calculations. 

# When using inequalities, just use the <, >, <=, >= signs. Don't use \leq or \geq. Don't include any words or any other symbols. """
    
# print(api_call(prompt = prompt))
    
# prompt = """Consider this series: \[
#     \sum_{d=0}^{\infty} \frac{2d + 1}{2h^2 \left( 1 + \frac{d(d+1)}{h^2} \right) \left( 1 + \frac{d(d+1)}{h^2 m^2} \right)^2} \ll 1 + \log(m^2)
#     \] for h, m \geq 1. Give me values for d_0=0,d_1, d_2,..,d_k=Infinity such that if S_d_k is defined 
#     as the sum from d=d_{k} to d_{k+1}, then proving this estimate for each S_{d_i} becomes very easy. Here << means that there exists
#     a positive constant C>0 such that the left side <= C. right side, for all h,m\geq 1. I only want the output as [d_1,d_2,...,d_k]. Don't give me any more words. Don't include 0 or infinity in your answer. Don't put any signs or anything apart from 
#     just the array. When you sare multiplying variables, don't forget to include * between them"""
# result = api_call(prompt=prompt, parse=True)
# for a in result:
#     print(a)

# Alright, let's make everything systematic. Wrap it up in a function that can be called. 
# Don't write down any executables. But we can write down a class. 

@dataclass
class inequality:
    variables: str
    domain_description: str
    lhs: str
    rhs: str
    
inequality_1 = inequality(variables = "x, y", domain_description="x>0, y>1", lhs= "x*y", rhs = "y*Log[y]+exp[x]")
inequality_2 = inequality(variables = "x,y,z", domain_description = "x>0, y>0, z>0", lhs = "(x*y*z)^(1/3)", rhs = "(x+y+z)/3")
# res = attempt_proof(inequality_1.variables, inequality_1.domain_description+", x <= 2 Log[y]", inequality_1.lhs, inequality_1.rhs)
# print(res)


def try_and_prove(inequality: inequality):
    prompt = f"""<code_editing_rules>
  <guiding_principles>
    – Be precise, avoid conflicting instructions
    – Use natural subdomains so inequality proof is trivial
    – Minimize the number of subdomains
    – Output only subdomains, no extra words or symbols
    – Use only <=, >=, <, >, Log[], Exp[] in the output. 
    Only use Mathematical notation that the software Mathematica can parse
  </guiding_principles>

  <task>
    Given domain: {inequality.domain_description}
    Inequality: {inequality.lhs} <= {inequality.rhs}
    Find minimal subdomains that make proving the inequality/asymptotic estimate trivial.
    The union of these subdomains should be the whole domain.
  </task>

  <output_format>
    [{' && '.join([p.strip() for p in inequality.domain_description.split(',')])} && subdomain1, {' && '.join([p.strip() for p in inequality.domain_description.split(',')])} && subdomain2, ...]. Hence, your output should in the form of an array
  </output_format>
</code_editing_rules>
"""
    res = api_call(prompt=prompt)
    if res and res[0] == '[' and res[-1] == ']':
        inner = res[1:-1].strip()
        print(inner)

        # Try to split into subdomain items robustly
        items: List[str] = []
        if '},' in inner:
            raw_items = [s + '}' if not s.strip().endswith('}') else s for s in inner.split('},')]
            items = [it.strip() for it in raw_items if it.strip()]
        else:
            # Split on commas that are followed by a '{' (common LLM format)
            parts = re.split(r",\s*(?=\{)", inner)
            items = [p.strip() for p in parts if p.strip()]

        # Prepare base domain as a flat list of conditions (no braces)
        base_parts = [p.strip() for p in inequality.domain_description.strip().strip('{}').split(',') if p.strip()]

        results = []
        for sd in items:
            sd_inner = sd.strip()
            # If the subdomain echoes the base domain then '&& ...', drop the echo
            m = re.match(r"^\{[^}]*\}\s*&&\s*(.*)$", sd_inner)
            if m:
                sd_inner = m.group(1).strip()
            # If wrapped in braces, strip them to get a flat condition
            if sd_inner.startswith('{') and sd_inner.endswith('}'):
                sd_inner = sd_inner[1:-1]
            # Build a single flat WL list of conditions
            conds_combined = '{' + ', '.join(base_parts + [sd_inner]) + '}'
            out = attempt_proof(inequality.variables, conds_combined, inequality.lhs, inequality.rhs)
            print(f"The proof attempt in {{{sd_inner}}} : {out}")
            results.append(out == 'It is proved')

        if results and all(results):
            print('Proved everywhere')
        



    
if __name__ == "__main__":
    print('hello')
    

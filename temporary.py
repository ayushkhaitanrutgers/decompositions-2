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

    # Common install locations
    for p in ("/usr/local/bin/wolframscript", "/opt/homebrew/bin/wolframscript"):
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
def attempt_proof(vars,conds, lhs, rhs):
    # Demo usages
    for c in range(1):
        status= False
        # normalize WL heads without changing math content
        lhs_wl = lhs.replace('exp[', 'Exp[').replace('log[', 'Log[')
        rhs_wl = rhs.replace('exp[', 'Exp[').replace('log[', 'Log[')
        # ensure proper braces/sequence for vars and conds
        vars_text = vars.strip()
        if vars_text.startswith('{') and vars_text.endswith('}'):
            vars_text = vars_text[1:-1]
        conds_text = conds.strip()
        if conds_text.startswith('{') and conds_text.endswith('}'):
            conds_text = conds_text[1:-1]
        a=wl_eval(f"""witnessBigO[vars_, conds_, lhs_, rhs_, c_] := 
  Module[{{S}}, S = If[conds === {{}}, True, And @@ conds];
   Resolve[ForAll[vars, Implies[S, lhs <= 10^c*rhs]], Reals]];

witnessBigO[{{{vars_text}}}, {{{conds_text}}}, {lhs_wl}, {rhs_wl}, {c}]
    """)
        if a == 'True':
            status = True
            return 'It is proved'
            break
        elif a == 'False':
            status = True
            return 'This is False'
        else:
            continue
    if status == False:
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
class question:
    variables: str
    domain_description: str
    lhs: str
    rhs: str
    
question_1 = question(variables = "x, y", domain_description="x>0, y>1", lhs= "x*y", rhs = "y*Log[y]+exp[x]")
question_2 = question(variables = "x,y,z", domain_description = "x>0, y>0, z>0", lhs = "(x*y*z)^(1/3)", rhs = "(x+y+z)/3")
# res = attempt_proof(question_1.variables, question_1.domain_description+", x <= 2 Log[y]", question_1.lhs, question_1.rhs)
# print(res)


# def try_and_prove(question : question):
#     prompt = f"""<code_editing_rules>
#   <guiding_principles>
#     – Be precise, avoid conflicting instructions
#     – Use natural subdomains so inequality proof is trivial
#     – Minimize the number of subdomains
#     – Output only subdomains, no extra words or symbols
#     – Use only <=, >=, <, >, Log[], Exp[] in the output. 
#     Only use Mathematical notation that the software Mathematica can parse
#   </guiding_principles>

#   <task>
#     Given domain: {question.domain_description}
#     Inequality: {question.lhs} <= {question.rhs}
#     Find minimal subdomains that make proving the inequality/asymptotic estimate trivial.
#     The union of these subdomains should be the whole domain.
#   </task>

#   <output_format>
#     [{' && '.join([p.strip() for p in question.domain_description.split(',')])} && subdomain1, {' && '.join([p.strip() for p in question.domain_description.split(',')])} && subdomain2, ...]. Hence, your output should in the form of an array
#   </output_format>
# </code_editing_rules>
# """
#     res = api_call(prompt=prompt)
#     if res:
#         if res[0]=='[' and res[-1]==']':
#             res = res[1:-1]
#             print(res)
#             temp_arr = [element.strip() for element in res.split(',')]
#             if len(temp_arr)!=0:
#                 count = 0
#                 for num in range(len(temp_arr)):
#                     print(f"""The proof attempt in {temp_arr[num]} : {attempt_proof(question.variables, question.domain_description+f', {temp_arr[num]}', question.lhs, question.rhs)}""")
#                     count+=1
#                 if count == len(temp_arr):
#                     print('Proved everywhere')
        

#################### We now try and prove that series can also be solved. We first need to make an API call. 

@dataclass
class series_to_bound:
    formula : str
    summation_index: str
    summation_bounds: List[str]
    conjectured_upper_asymptotic_bound: str
    
series_1 = series_to_bound(formula = "(2*d+1)/(2*h^2*(1+d*(d+1)/(h^2))(1+d*(d+1)/(h^2*m^2))^2)", summation_index="d", summation_bounds=["0","Infinity"], conjectured_upper_asymptotic_bound="1+Log[m^2]")
    

# def ask_llm_series(series: series_to_bound):
#     prompt = f"""<code_editing_rules>
#     <guiding_principles>
#         – Be precise; avoid conflicting or circular instructions.
#         – Choose “natural” breakpoint scales where the term behavior changes (e.g., dominance switches, monotonicity kicks in, easy comparison with p-series/geometric/integral bounds).
#         – Minimize the number of breakpoints while ensuring the final bound is straightforward on each subrange.
#         – Cover the full index range from 0 to Infinity, with nonoverlapping, contiguous subranges.
#         – If the index is integer-valued, return integer breakpoints using Floor[]/Ceiling[] when needed.
#         – Breakpoints may depend only on constants/parameters that appear in the series description.
#         – Use only Mathematica-parsable expressions for breakpoints, built from numbers, parameters, +, -, *, /, ^, Log[], Exp[], Sqrt[], Max[], Min[], Floor[], Ceiling[].
#         – Output only the breakpoint list; no extra words, symbols, or justification.
#     </guiding_principles>

#     <task>
#         We are given a series described by:
#         • formula: {series.formula}
#         • summation index: {series.summation_index}
#         • summation_bounds: {series.summation_bounds}
#         • conjectured_upper_asymptotic_bound: {series.conjectured_upper_asymptotic_bound}
#         • Import definition to understand: Given two functions f and g, f << g means that there exists a positive constant C>0 such that f <= C*g everywhere in the domain
        

#         Goal: Return a minimal list of breakpoints [0, d_1, …, d_n, Infinity] such that proving
#         Sum[formula, summation_bounds restricted to each consecutive subrange]
#         << conjectured_upper_asymptotic_bound
#         is trivial on every subrange (e.g., via a simple termwise bound, a direct comparison to a standard convergent series, or the integral test with monotonicity).
#     </task>

#     <requirements_for_breakpoints>
#         – Start at 0 and end at Infinity.
#         – Strictly nondecreasing: 0 <= d_1 <= … <= d_n < Infinity.
#         – Each d_i must be a closed-form Mathematica expression in the series’ parameters (if any), using only the allowed constructors above.
#         – Prefer canonical scales (e.g., powers/roots of parameters, thresholds defined by equating dominant terms) that make comparisons immediate.
#         – Keep the list as short as possible while preserving triviality of the bound on each subrange.
#     </requirements_for_breakpoints>

#     <output_format>
#         [0, d1, d2, ..., Infinity]
#         # Return a list with the breakpoints only.
#     </output_format>
#     </code_editing_rules>
#     """
#     print(api_call_series(prompt=prompt))
    
# if __name__ == "__main__":
#     ask_llm_series(series_1)

    


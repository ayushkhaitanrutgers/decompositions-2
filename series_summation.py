import subprocess, shlex, os, shutil, json
from typing import Any, List
from llm_client import api_call, api_call_series
from dataclasses import dataclass
import re
import tempfile, pathlib, subprocess, os

def wl_run_file(code: str, form: str = "InputForm") -> str:
    env = _clean_env()
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "script.wl"
        # Wrap in ToString[...] here so the -file returns plain text
        p.write_text(f"ToString[\n(\n{code}\n), {form}\n]")
        out = subprocess.check_output([WOLFRAMSCRIPT, "-file", str(p)], text=True, env=env)
    return out.strip()


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
    
@dataclass
class series_to_bound:
    formula : str
    conditions : str
    summation_index: str
    other_variables: str
    summation_bounds: List[str]
    conjectured_upper_asymptotic_bound: str
    

    

def ask_llm_series(series: series_to_bound):
    prompt = f"""<code_editing_rules>
    <guiding_principles>
        – Be precise; avoid conflicting or circular instructions.
        – Choose “natural” breakpoint scales where the term behavior changes (e.g., dominance switches, monotonicity kicks in, easy comparison with p-series/geometric/integral bounds).
        – Minimize the number of breakpoints while ensuring the final bound is straightforward on each subrange.
        – Cover the full index range from 0 to Infinity, with nonoverlapping, contiguous subranges.
        – Do not use Floor[]/Ceiling[], etc. Just return the values as natural algebraic expressions. Also, algebraically simplify everything. For example, Sqrt[a^2] can be written as a. Assume everything is positive.
        – Breakpoints may depend only on constants/parameters that appear in the series description.
        – Use only Mathematica-parsable expressions for breakpoints, built from numbers, parameters, +, -, *, /, ^, Log[], Exp[], Sqrt[].
        – Output only the breakpoint list; no extra words, symbols, or justification.
    </guiding_principles>

    <task>
        We are given a series described by:
        • formula: {series.formula}
        • summation index: {series.summation_index}
        • summation_bounds: {series.summation_bounds}
        • conjectured_upper_asymptotic_bound: {series.conjectured_upper_asymptotic_bound}
        • Import definition to understand: Given two functions f and g, f << g means that there exists a positive constant C>0 such that f <= C*g everywhere in the domain
        

        Goal: Return a minimal list of breakpoints [0, d_1, …, d_n, Infinity] such that proving
        Sum[formula, summation_bounds restricted to each consecutive subrange]
        << conjectured_upper_asymptotic_bound
        is trivial on every subrange (e.g., via a simple termwise bound, a direct comparison to a standard convergent series, or the integral test with monotonicity).
    </task>

    <requirements_for_breakpoints>
        – Start at 0 and end at Infinity.
        – Strictly nondecreasing: 0 <= d_1 <= … <= d_n < Infinity.
        – Each d_i must be a closed-form expression in the series parameters (if any), using only the allowed constructors above.
        – Prefer canonical scales (e.g., powers/roots of parameters, thresholds defined by equating dominant terms) that make comparisons immediate. Also, algebraically simplify the break points as possible.
        – Keep the list as short as possible while preserving triviality of the bound on each subrange.
    </requirements_for_breakpoints>

    <output_format>
        [0, d1, d2, ..., Infinity]
        # Return a list with the breakpoints only.
    </output_format>
    </code_editing_rules>
    """
    response = api_call_series(prompt=prompt)
    if response[0]=='[' and response[-1]==']':
        response = '{'+response[1:-1]+'}'
    print(response)
    
    count=0
    
    for c in range(5):

        a = wl_eval (f"""
        Clear[LeadingSummand, DominancePiecewise, LeastSummand, \
        AntiDominancePiecewise,
            expandPowersInProductNoNumbers, reducedForm, createAssums, \
        calculateEstimates];

        LeadingSummand[sum_, assum_] := Module[{{terms, vars, dominatesQ, \
        winners}},
        terms = DeleteCases[List @@ Expand[sum], 0];
        If[!ListQ[terms], terms = {{terms}}];
        If[terms === {{}}, Return[0]];
        If[Length[terms] == 1, Return[First[terms]]];
        vars = Variables[{{sum, assum}}];
        dominatesQ[t_] := Resolve[
            ForAll[vars, Implies[assum, And @@ Thread[t >= DeleteCases[terms, \
        t, 1, 1]]]],
            Reals
        ];
        winners = Select[terms, TrueQ @ dominatesQ[#] &];
        Which[winners =!= {{}}, First[winners],
                True, Simplify[DominancePiecewise[terms, assum, vars], \
        assum]]
        ];

        DominancePiecewise[terms_, assum_, vars_] := Module[{{conds}},
        conds = Table[
            Reduce[assum && And @@ Thread[ti >= DeleteCases[terms, ti, 1, \
        1]], vars, Reals],
            {{ti, terms}}
        ];
        Piecewise[Transpose[{{terms, conds}}]]
        ];

        LeastSummand[sum_, assum_] := Module[{{terms, vars, leastQ, winners}},
        terms = DeleteCases[List @@ Expand[sum], 0];
        If[!ListQ[terms], terms = {{terms}}];
        If[terms === {{}}, Return[0]];
        If[Length[terms] == 1, Return[First[terms]]];
        vars = Variables[{{sum, assum}}];
        leastQ[t_] := Resolve[
            ForAll[vars, Implies[assum, And @@ Thread[t <= DeleteCases[terms, \
        t, 1, 1]]]],
            Reals
        ];
        winners = Select[terms, TrueQ @ leastQ[#] &];
        Which[winners =!= {{}}, First[winners],
                True, Simplify[AntiDominancePiecewise[terms, assum, vars], \
        assum]]
        ];

        AntiDominancePiecewise[terms_, assum_, vars_] := Module[{{conds}},
        conds = Table[
            Reduce[assum && And @@ Thread[ti <= DeleteCases[terms, ti, 1, \
        1]], vars, Reals],
            {{ti, terms}}
        ];
        Piecewise[Transpose[{{terms, conds}}]]
        ];

        expandPowersInProductNoNumbers[expr_] :=
        Select[
            Replace[List @@ expr,
            Power[base_, n_Integer?Positive] :> Sequence @@ \
        ConstantArray[base, n],
            {{1}}
            ],
            Not[NumericQ[#]] &
        ];

        reducedForm[expr_, assum_] := Module[{{numr, denr, simpn, simpd}},
        numr = expandPowersInProductNoNumbers @ Numerator @ Simplify[expr, \
        Assumptions -> assum];
        denr = expandPowersInProductNoNumbers @ Denominator @ \
        Simplify[expr, Assumptions -> assum];
        simpn = Times @@ (LeadingSummand[#, assum] & /@ numr);
        simpd = Times @@ (LeadingSummand[#, assum] & /@ denr);
        Simplify[simpn/simpd, Assumptions -> assum]
        ];

        createAssums[baseAssums_, points_] := Module[{{p}},
        p = Partition[points, 2, 1];
        baseAssums && d > #[[1]] && d < #[[2]] & /@ p
        ];

        calculateEstimates[expr_, baseAssums_, points_] := Module[{{assums, \
        part}},
        assums = createAssums[baseAssums, points];
        part   = Prepend[#, d] & /@ Partition[points, 2, 1];
        MapThread[
            Integrate[reducedForm[expr, #1], #2, Assumptions -> #1] &,
            {{assums, part}}
        ]
        ];

        res1 = Flatten@calculateEstimates[{series.formula}, {' && '.join([series.summation_index+">1", series.conditions])},{response}];

        res2= Resolve[ForAll[{series.other_variables}, 
            Implies[{series.conditions}, # <= 10^{c}*{series.conjectured_upper_asymptotic_bound}]], Reals] & /@ res1;
            
        If[AllTrue[res2,TrueQ],True,res2]
        """)
        if a == "True":
            print('All estimates verified')
            break
        else:
            count+=1
            print('Not verified')
    if count ==5:
        print('Try prompting the LLM again. The verification has failed up to a positive constant C = 10^4')
    
series_1 = series_to_bound(formula = "(2*d+1)/(2*h^2*(1+d*(d+1)/(h^2))(1+d*(d+1)/(h^2*m^2))^2)", conditions = "h >1 && m > 1", summation_index="d", other_variables="{h,m}", summation_bounds=["0","Infinity"], conjectured_upper_asymptotic_bound="1+Log[m^2]")

if __name__ == "__main__":
    ask_llm_series(series_1)




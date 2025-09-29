import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, List
import pathlib
import re
import subprocess

from llm_client import api_call, api_call_series
import mathematica_export as wl


def wl_run_file(code: str, form: str = "InputForm") -> str:
    """Execute multi-line Wolfram code either via cloud or local kernel."""

    wrapped = f"ToString[\n(\n{code}\n), {form}\n]"
    if getattr(wl, "_USE_WOLFRAM_CLOUD", False):
        print("[wolfram] Using Wolfram Cloud endpoint", flush=True)
        return wl._cloud_eval(wrapped)  # type: ignore[attr-defined]

    if not getattr(wl, "WOLFRAMSCRIPT", None):
        raise RuntimeError("wolframscript binary unavailable for local execution")

    env = wl._clean_env()  # type: ignore[attr-defined]
    with tempfile.TemporaryDirectory() as td:
        script_path = pathlib.Path(td) / "script.wl"
        script_path.write_text(wrapped)
        cmd = [wl.WOLFRAMSCRIPT, "-file", str(script_path)]
        print(f"[wolfram] Using local wolframscript {wl.WOLFRAMSCRIPT}", flush=True)
        return subprocess.check_output(cmd, text=True, env=env).strip()

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
        a = wl.wl_eval(f"""witnessBigO[vars_, conds_, lhs_, rhs_, c_] := 
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
        

        Goal: Return a minimal list of breakpoints [{series.summation_bounds[0]}, d_1, …, d_n, {series.summation_bounds[1]}] such that proving
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
        [{series.summation_bounds[0]}, d1, d2, ..., {series.summation_bounds[1]}]
        # Return a list with the breakpoints only.
    </output_format>
    </code_editing_rules>
    """
    response = api_call_series(prompt=prompt)
    if response[0]=='[' and response[-1]==']':
        response = '{'+response[1:-1]+'}'
    print(response)
    
    count=0
    
    paclet_setup = (
        """
        Needs["PacletManager`"];
        Quiet[Check[PacletUninstall["UnitTable"], Null]];
        Quiet[Check[PacletInstall["UnitTable"], Null]];
        """
        if not getattr(wl, "_USE_WOLFRAM_CLOUD", False)
        else "Quiet[Check[Needs[\"UnitTable`\"], Null]];"
    )

    for c in range(5):
        ante_code = "True" if getattr(series, "conditions", "") == "" else series.conditions
        vars_text = "True" if getattr(series, "other_variables", "") == "" else series.other_variables

        if vars_text.startswith("{") and vars_text.endswith("}"):
            vars_text = vars_text[1:-1].strip()
        result_packet = wl.wl_eval_json(
        f"""
        Clear[LeadingSummand, DominancePiecewise, LeastSummand, 
        AntiDominancePiecewise, expandPowersInProductNoNumbers, reducedForm,
        createAssums, calculateEstimates, expr, baseAssums];

        logMessages = Table[Null, {0}];
        log[s_String] := AppendTo[logMessages, s];
        logForm[label_String, expr_] := log[label <> ": " <> ToString[expr, InputForm]];

        {paclet_setup}
        
        termsOfSum[expr_] := 
        Module[{{e = Expand[expr]}}, If[Head[e] === Plus, List @@ e, {{e}}]];

        LeadingSummand[sum_, assum_] := 
        Module[{{terms, vars, dominatesQ, winners}}, 
        terms = DeleteCases[termsOfSum[sum], 0];
        If[terms === {{}}, Return[0]];
        If[Length[terms] == 1, Return[First[terms]]];
        vars = Variables[{{sum, assum}}];
        dominatesQ[t_] := 
            Resolve[ForAll[vars, 
            Implies[assum, And @@ Thread[t >= DeleteCases[terms, t, 1, 1]]]],
            Reals];
        winners = Select[terms, TrueQ@dominatesQ[#] &];
        Which[winners =!= {{}}, First[winners], True, 
            Simplify[DominancePiecewise[terms, assum, vars], assum]]];

        DominancePiecewise[terms_, assum_, vars_] := 
        Module[{{conds}}, 
        conds = Table[
            Reduce[assum && And @@ Thread[ti >= DeleteCases[terms, ti, 1, 1]],
            vars, Reals], {{ti, terms}}];
        Piecewise[Transpose[{{terms, conds}}]]];

        LeastSummand[sum_, assum_] := 
        Module[{{terms, vars, leastQ, winners}}, 
        terms = DeleteCases[termsOfSum[sum], 0];
        If[terms === {{}}, Return[0]];
        If[Length[terms] == 1, Return[First[terms]]];
        vars = Variables[{{sum, assum}}];
        leastQ[t_] := 
            Resolve[ForAll[vars, 
            Implies[assum, And @@ Thread[t <= DeleteCases[terms, t, 1, 1]]]],
            Reals];
        winners = Select[terms, TrueQ@leastQ[#] &];
        Which[winners =!= {{}}, First[winners], True, 
            Simplify[AntiDominancePiecewise[terms, assum, vars], assum]]];

        AntiDominancePiecewise[terms_, assum_, vars_] := 
        Module[{{conds}}, 
        conds = Table[
            Reduce[assum && And @@ Thread[ti <= DeleteCases[terms, ti, 1, 1]],
            vars, Reals], {{ti, terms}}];
        Piecewise[Transpose[{{terms, conds}}]]];

        (*robust factor extractor:always returns a list of non-\
        numeric factors*)
        expandPowersInProductNoNumbers[expr_] := 
        Module[{{factors}}, 
        factors = If[Head[expr] === Times, List @@ expr, {{expr}}];
        factors = 
            factors /. 
            Power[base_, n_Integer?Positive] :> ConstantArray[base, n];
        factors = Flatten[factors];
        Select[factors, Not@*NumericQ]];


        reducedFormIndexed[expr_, assum_, idx_] := 
        Module[{{numr, denr, simpn, simpd}}, 
        numr = expandPowersInProductNoNumbers@
            Numerator@Simplify[expr, Assumptions -> assum];
        denr = 
            expandPowersInProductNoNumbers@
            Denominator@Simplify[expr, Assumptions -> assum];
        simpn = Times @@ (LeadingSummand[#, assum] & /@ numr);
        simpd = Times @@ (LeadingSummand[#, assum] & /@ denr);
        logForm["  Numerator factors", numr];
        logForm["  Denominator factors", denr];
        logForm["  Leading term in numerator in subdomain_"<>ToString[idx], simpn];
        logForm["  Leading term in denominator in subdomain_"<>ToString[idx], simpd];
        Simplify[simpn/simpd, Assumptions -> assum]];

        createAssums[baseAssums_, points_] := 
        Module[{{p}}, p = Partition[points, 2, 1];
        baseAssums && d > #[[1]] && d < #[[2]] & /@ p];

        calculateEstimates[expr_, baseAssums_, points_] := 
        Module[{{assums, part}}, assums = createAssums[baseAssums, points];
        part = Prepend[#, d] & /@ Partition[points, 2, 1];
        log["\n== Verification run =="]; 
        logForm["Formula", expr];
        logForm["Base assumptions", baseAssums];
        logForm["Breakpoints", points];
        Do[logForm["Subdomain "<>ToString[i], assums[[i]]], {{i, Length[assums]}}];
        MapThread[
            Integrate[reducedFormIndexed[expr, #1, #3], #2, 
            Assumptions -> #1] &, {{assums, part, Range[Length[assums]]}}]];

        
        baseAssumptions = {' && '.join([series.summation_index+">1", series.conditions])};
        res1 = Flatten@calculateEstimates[{series.formula}, baseAssumptions,{response}];

        log["Trying constant C = "<>ToString[10^{c}, InputForm]];
        res2= Resolve[ForAll[{series.other_variables}, 
            Implies[{series.conditions}, # <= 10^{c}*{series.conjectured_upper_asymptotic_bound}]], Reals] & /@ res1;
        logForm["Resolve results", res2];
            
        <|"Logs" -> logMessages, "Result" -> If[AllTrue[res2, TrueQ], True, res2]|>
        """)

        for line in result_packet.get("Logs", []):
            print(line)

        a = result_packet.get("Result")
        if a is True:
            print("All estimates verified")
            break
        else:
            count += 1
            print("Not verified")
    if count == 5:
        print("Try prompting the LLM again. The verification has failed up to a positive constant C = 10^4")
    
series_1 = series_to_bound(formula = "(2*d+1)/(2*h^2*(1+d*(d+1)/(h^2))(1+d*(d+1)/(h^2*m^2))^2)", conditions = "h >1 && m > 1", summation_index="d", other_variables="{h,m}", summation_bounds=["0","Infinity"], conjectured_upper_asymptotic_bound="1+Log[m^2]")

# --- CLI entrypoint ---
def main() -> None:
    import argparse
    import inspect

    parser = argparse.ArgumentParser(
        prog="decomp",
        description="Run LLM-guided series decomposition and CAS verification",
    )
    parser.add_argument(
        "example",
        nargs="?",
        help="Name of the example object from examples.py (e.g., series_1)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available examples and exit",
    )
    args = parser.parse_args()

    try:
        import examples  # your examples live here
    except Exception as e:
        raise SystemExit(f"Failed to import examples.py: {e}")

    # Collect public attributes that are instances of series_to_bound
    available = {
        name: obj
        for name, obj in vars(examples).items()
        if not name.startswith("_") and isinstance(obj, series_to_bound)
    }

    if args.list or not args.example:
        if not available:
            print("No examples found in examples.py")
            return
        print("Available examples:")
        for name in sorted(available):
            print(f"  - {name}")
        return

    obj = available.get(args.example)
    if obj is None:
        close = ", ".join(sorted(available)) or "<none>"
        raise SystemExit(f"Unknown example '{args.example}'. Choose one of: {close}")

    ask_llm_series(obj)


if __name__ == "__main__":
    main()

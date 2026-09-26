import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, List, Optional
import pathlib
import re
import subprocess

import mathematica_export as wl
from prover import prove_bigO, split_conditions, to_wolfram


def api_call_series(prompt: str, **kwargs):
    """Ask the LLM (imported lazily so the module loads without google-genai)."""
    from llm_client import api_call_series as _api_call_series

    return _api_call_series(prompt=prompt, **kwargs)


def _substitute_index(formula: str, index: str, replacement: str) -> str:
    """Replace the summation index (as a whole word) by `replacement`.

    The old code did formula.replace('d', '-d'), which also rewrote the 'd'
    inside names such as 'Round' or a parameter called 'delta', and ignored
    the actual index name."""
    return re.sub(r"(?<![A-Za-z0-9_])" + re.escape(index) + r"(?![A-Za-z0-9_])", replacement, formula)


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
        return wl._run_with_timeout(cmd, wl._WOLFRAM_TIMEOUT).strip()  # type: ignore[attr-defined]

def attempt_proof(vars, conds, lhs, rhs):
    """Kept for backwards compatibility; the robust prover does the work now."""
    return wl.attempt_proof(vars, conds, lhs, rhs)


@dataclass
class series_to_bound:
    formula : str
    conditions : str
    summation_index: str
    other_variables: str
    summation_bounds: List[str]
    conjectured_upper_asymptotic_bound: str
    breakpoints: Optional[List[str]] = None   # optional: skip the LLM and use these
    

    

def ask_llm_series(series: series_to_bound):
    
    if series.summation_bounds[0][0]=='-' and series.summation_bounds[1][0]=='-':
        series_temp = series_to_bound(
            formula=_substitute_index(series.formula, series.summation_index, "(-" + series.summation_index + ")"),
            conditions=series.conditions,
            summation_index=series.summation_index,
            other_variables=series.other_variables,
            summation_bounds=[series.summation_bounds[1][1:], series.summation_bounds[0][1:]],
            conjectured_upper_asymptotic_bound=series.conjectured_upper_asymptotic_bound,
        )
        
        ask_llm_series(series_temp)
        return
        
    if series.summation_bounds[0][0]=='-' and not series.summation_bounds[1][0]=='-':
        series_temp_1 = series_to_bound(
            formula=_substitute_index(series.formula, series.summation_index, "(-" + series.summation_index + ")"),
            conditions=series.conditions,
            summation_index=series.summation_index,
            other_variables=series.other_variables,
            summation_bounds=["0", series.summation_bounds[0][1:]],
            conjectured_upper_asymptotic_bound=series.conjectured_upper_asymptotic_bound,
        )
        
        series_temp_2 = series_to_bound(
            formula=series.formula,
            conditions=series.conditions,
            summation_index=series.summation_index,
            other_variables=series.other_variables,
            summation_bounds=["0", series.summation_bounds[1]],
            conjectured_upper_asymptotic_bound=series.conjectured_upper_asymptotic_bound,
        )
        print(f"First we prove the estimate for the negative part of the series in the range [{series.summation_bounds[0]},0]")
        ask_llm_series(series_temp_1)
        print(f"Now we prove the estimate for the positive part of the series in the range [0,{series.summation_bounds[1]}]")
        ask_llm_series(series_temp_2)
        return
        
        

    
    prompt = f"""<code_editing_rules>
    <guiding_principles>
        – Be precise; avoid conflicting or circular instructions.
        – Choose “natural” breakpoint scales where the term behavior changes (e.g., dominance switches, monotonicity kicks in, easy comparison with p-series/geometric/integral bounds).
        – Minimize the number of breakpoints while ensuring the final bound is straightforward on each subrange.
        – Cover the full index range from {series.summation_bounds[0]} to {series.summation_bounds[1]}, with nonoverlapping, contiguous subranges.
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
        – Strictly nondecreasing: {series.summation_bounds[0]} <= d_1 <= … <= d_n < {series.summation_bounds[1]}.
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
    breakpoints = getattr(series, "breakpoints", None)
    if breakpoints:
        # Breakpoints given by hand: no LLM call needed.
        response = "[" + ", ".join(str(b) for b in breakpoints) + "]"
    else:
        try:
            response = api_call_series(prompt=prompt)
        except Exception as exc:
            print(f"Failed to obtain breakpoints from the LLM: {exc}")
            print("Tip: give them by hand with series_to_bound(..., breakpoints=[...]).")
            return
    response = (response or "").strip()
    if not (response.startswith("[") and response.endswith("]")):
        print(f"Could not read a list of breakpoints from: {response!r}")
        return
    response = "{" + response[1:-1] + "}"
    print("Breakpoints:", response)
    
    # The old code re-installed the "UnitTable" paclet on every run, which
    # needs internet access and can hang on a cluster node.  Nothing here
    # uses units, so just load it if it is already present, with a time limit.
    paclet_setup = 'Quiet[TimeConstrained[Check[Needs["UnitTable`"], Null], 5, Null]];'

    ante_code = "True" if getattr(series, "conditions", "") == "" else series.conditions
    vars_text = "True" if getattr(series, "other_variables", "") == "" else series.other_variables

    if vars_text.startswith("{") and vars_text.endswith("}"):
        vars_text = vars_text[1:-1].strip()

    # The summation index runs over the actual summation range.  (The old
    # code assumed index > 1 whatever the bounds were, so for a sum starting
    # at 0 the leading term was chosen under a wrong assumption.)
    lo, hi = series.summation_bounds[0], series.summation_bounds[1]
    index_range = " && ".join(
        ([f"{series.summation_index} >= {lo}"] if lo != "-Infinity" else [])
        + ([f"{series.summation_index} <= {hi}"] if hi != "Infinity" else [])
    ) or "True"

    # One Mathematica call computes the estimate for every subrange (the
    # integrals).  The old code repeated this whole computation five times,
    # once per constant 10^c, and used Implies[] inside ForAll[], which makes
    # Resolve[] fail on most non-polynomial statements.
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
    factors = Replace[factors,
    Power[base_, n_Integer?Positive] :> ConstantArray[base, n],
    {{1}} (* only the immediate elements of factors *)
    ];
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
    baseAssums && {series.summation_index} > #[[1]] && {series.summation_index} < #[[2]] & /@ p];

    calculateEstimates[expr_, baseAssums_, points_] := 
    Module[{{assums, part}}, assums = createAssums[baseAssums, points];
    part = Prepend[#, {series.summation_index}] & /@ Partition[points, 2, 1];
    log["\n== Verification run =="]; 
    logForm["Formula", expr];
    logForm["Base assumptions", baseAssums];
    logForm["Breakpoints", points];
    Do[logForm["Subdomain "<>ToString[i], assums[[i]]], {{i, Length[assums]}}];
    MapThread[
        Integrate[reducedFormIndexed[expr, #1, #3], #2, 
        Assumptions -> #1] &, {{assums, part, Range[Length[assums]]}}]];

    
    baseAssumptions = {' && '.join([index_range] + ([series.conditions] if ante_code != "True" else []))};
    res1 = Flatten@calculateEstimates[{series.formula}, baseAssumptions,{response}];

    <|"Logs" -> logMessages, "Estimates" -> (ToString[#, InputForm] & /@ res1)|>
    """)

    for line in result_packet.get("Logs", []):
        print(line)
    estimates = result_packet.get("Estimates") or []
    if not estimates:
        print("Not verified: Mathematica returned no estimates.")
        return

    # Each estimate must be << the conjectured bound.  The robust prover tries
    # constants 1, 2, 4, 10, ..., 10^6 and several methods, with time limits.
    all_ok = True
    for i, est in enumerate(estimates, start=1):
        print(f"Estimate for subrange {i}: {est}")
        if "Integrate[" in est or "ConditionalExpression" in est or "Piecewise" in est:
            print("  Not verified: the integral has no usable closed form.")
            all_ok = False
            continue
        r = prove_bigO(est, series.conjectured_upper_asymptotic_bound, vars_text, ante_code)
        print("  " + str(r).replace("\n", "\n  "))
        if r.status != "proved":
            all_ok = False
    if all_ok:
        print("All estimates verified")
    else:
        print("Not verified. Try prompting the LLM again, or a different set of breakpoints.")

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

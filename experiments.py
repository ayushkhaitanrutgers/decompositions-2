"""
Helpers to parse free-form/LaTeX series descriptions into a
`series_to_bound` object using the project LLM client.

This keeps all logic self-contained here, without touching other files.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple, Literal

from llm_client import api_call, generate_text
from series_summation import series_to_bound


_SYSTEM = (
    "You transform math text into a strict JSON spec understood by Mathematica. "
    "Return only compact JSON, no prose. Use Mathematica syntax: Log[], Exp[], Sqrt[], Infinity, ^, *, /, +, -. "
    "Ensure symbols are simple ASCII identifiers."
)


def _extract_json(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from `text` and load it.

    Tolerates accidental prose by finding the first {...} block.
    """
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("No JSON object found in model output")
    s = m.group(0)
    return json.loads(s)


_CLASSIFIER_SYSTEM = (
    "You classify mathematical prompts for a decomposition tool. "
    "Always respond with compact JSON containing a single key 'kind'. "
    "Valid values are the lowercase strings 'series' or 'inequality'."
)


def classify_problem_kind(text: str, *, model: str = "gemini-2.5-flash") -> Literal["series", "inequality"]:
    """Use the LLM to decide whether the prompt describes a series or an inequality.

    The prompt provides extensive guidance so that sum descriptions with bounds
    (e.g., using `<<`) are still tagged as series.
    """

    prompt = (
        "Return ONLY JSON of the form {\"kind\":\"series\"} or {\"kind\":\"inequality\"}.\n"
        "\n"
        "Interpretation rules (follow strictly, prefer series when ambiguous):\n"
        "  • SERIES: The text introduces or references a summation/series (explicit Σ, \\sum, Sum[...], \"series\", \"summed from\", etc.), or asks to bound a series against another quantity. Even if the statement also contains inequality symbols like <<, ≤, ≪, treat it as SERIES because the core object is the sum.\n"
        "  • INEQUALITY: The text compares two expressions without describing a summation to analyze. Focus on proving or manipulating inequalities that do not hinge on evaluating a series.\n"
        "\n"
        "Additional guidance:\n"
        "  • If both a series description and inequality wording appear, choose \"series\".\n"
        "  • If the text names specific summation indices, ranges, or phrases like \"partial sums\", \"summed from\", \"series behaves like\", that is SERIES.\n"
        "  • If the text only references functions/variables with relations (<=, >=, <<) and no sum, classify as INEQUALITY.\n"
        "  • Do not infer a series unless it is explicit; likewise do not ignore an explicit Σ even if the main request is to bound it.\n"
        "\n"
        "Illustrations:\n"
        "  1. \"Prove Sum_{n=1}^\\infty 1/n^2 << 1.\"  -> {\"kind\":\"series\"}\n"
        "  2. \"Let f(x)=x^2. Show f(x) >= x for x >= 1.\"  -> {\"kind\":\"inequality\"}\n"
        "  3. \"\\sum_{d=1}^{\\infty} 1/d^2 \\ll 1, domain: True\"  -> {\"kind\":\"series\"}\n"
        "  4. \"Establish that for all positive reals a,b: (a+b)/2 >= sqrt(ab).\" -> {\"kind\":\"inequality\"}\n"
        "\n"
        "Text:\n"
        f"{text}\n"
    )
    raw = generate_text(
        prompt=prompt,
        system_instruction=_CLASSIFIER_SYSTEM,
        model=model,
        max_output_tokens=128,
    )
    spec = _extract_json(raw)
    kind = spec.get("kind", "")
    if not isinstance(kind, str):
        raise ValueError("Classifier returned invalid type")
    value = kind.strip().lower()
    if value not in ("series", "inequality"):
        raise ValueError(f"Unexpected classifier label: {kind!r}")
    return value  # type: ignore[return-value]


def parse_series(text: str, *, model: str = "gemini-2.5-flash") -> series_to_bound:
    """Parse a free-form (possibly LaTeX) description into `series_to_bound`.

    Inputs can be short sentences and/or a LaTeX snippet. The model returns
    a strict JSON object with the fields expected by `series_to_bound`.

    Required JSON keys (all strings except `summation_bounds`):
      - formula: Mathematica-parsable term for the summand (in terms of the index)
      - conditions: WL condition string (e.g., "h>1 && m>1")
      - summation_index: index symbol (e.g., "d")
      - other_variables: WL list of symbols in braces (e.g., "{h,m}")
      - summation_bounds: JSON list of two strings [lower, upper] (e.g., ["0","Infinity"]) or three for general Sum syntax
      - conjectured_upper_asymptotic_bound: WL expression (e.g., "1+Log[m^2]")
    """
    prompt = f"""
Return a strict JSON object with exactly these keys:
  formula: string (Mathematica syntax for the summand)
  conditions: string (Mathematica conditions, combined with &&)
  summation_index: string (single symbol)
  other_variables: string (Mathematica list of symbols in braces, e.g., {{h,m}})
  summation_bounds: array of strings (e.g., [\"0\",\"Infinity\"])
  conjectured_upper_asymptotic_bound: string (Mathematica expression)

Rules:
- Use only Mathematica-parsable syntax: Log[], Exp[], Sqrt[], Infinity, ^, *, /, +, -.
- Do not output any LaTeX markup like \\frac, \\sum; convert to Mathematica.
- Keep variable names as simple ASCII (a..z, A..Z, digits, underscores).
- Return ONLY compact JSON. No markdown, no code fences, no commentary.

Text to parse:
{text}
"""
    out = generate_text(prompt=prompt, system_instruction=_SYSTEM, model=model, max_output_tokens=512)
    spec = _extract_json(out)

    # Minimal normalization of fields
    def _req(k: str) -> str:
        if k not in spec or not isinstance(spec[k], str) or not spec[k].strip():
            raise ValueError(f"Missing or invalid field: {k}")
        return spec[k].strip()

    formula = _req("formula")
    conditions = _req("conditions")
    summation_index = _req("summation_index")
    other_variables = _req("other_variables")

    sb = spec.get("summation_bounds")
    if not isinstance(sb, list) or not sb:
        raise ValueError("summation_bounds must be a non-empty array of strings")
    summation_bounds = [str(x).strip() for x in sb]

    conj = _req("conjectured_upper_asymptotic_bound")

    return series_to_bound(
        formula=formula,
        conditions=conditions,
        summation_index=summation_index,
        other_variables=other_variables,
        summation_bounds=summation_bounds,
        conjectured_upper_asymptotic_bound=conj,
    )


def demo_parse() -> None:
    """Tiny demo: prints the parsed object from a sample LaTeX snippet.

    Use this as a quick check. Requires your API key to be configured.
    """
    latex_text = r"""
We study the series
\[ \sum_{d=0}^{\infty} \frac{2d+1}{2h^2\,\bigl(1+\frac{d(d+1)}{h^2}\bigr)\,\bigl(1+\frac{d(d+1)}{h^2 m^2}\bigr)^2} \]
for parameters h>1, m>1. Conjectured bound: 1 + \log(m^2).
    """
    obj = parse_series(latex_text)
    print(obj)


# ------------------------
# Deterministic text parser
# ------------------------

_INFINITY_WORDS = {"infinity", "Infinity", "+infinity", "+Infinity"}


def _normalize_wl_funcs(expr: str) -> str:
    """Normalize common function names to Mathematica heads with [ ].

    Only converts known function calls of the form name(args) to Name[args].
    Leaves ordinary parentheses intact.
    """
    s = expr
    for name, Name in (("log", "Log"), ("exp", "Exp"), ("sqrt", "Sqrt")):
        s = re.sub(rf"\b{name}\s*\((.*?)\)", rf"{Name}[\1]", s, flags=re.I)
    # Replace LaTeX-style ^{...} to ^(...)
    s = re.sub(r"\^\{([^}]*)\}", r"^(\1)", s)
    return s.strip()


def parse_series_text(text: str) -> series_to_bound:
    """Heuristic parser for the English string description of a series.

    Handles patterns like:
    "Consider the series: <formula>, where d is summed from 0 to infinity. The domain is h,m>=1. Should be bounded above by 1+log(m^2)"
    """
    t = " ".join(text.strip().split())

    # 1) Formula
    m = re.search(r"Consider the series:\s*(.*?)(?=,\s*where\b)", t, flags=re.I)
    if not m:
        raise ValueError("Could not find formula after 'Consider the series:'")
    formula_raw = m.group(1).strip()
    formula = _normalize_wl_funcs(formula_raw)

    # 2) Index and bounds
    m = re.search(r"where\s+([A-Za-z]\w*)\s+is\s+summed\s+from\s+([^\s]+)\s+to\s+([^\s\.]+)", t, flags=re.I)
    if not m:
        raise ValueError("Could not parse summation clause 'where <i> is summed from a to b'")
    idx = m.group(1)
    lower = m.group(2).rstrip(',')
    upper = m.group(3).rstrip(',')
    lower = lower
    upper = "Infinity" if upper in _INFINITY_WORDS else upper

    # 3) Domain / conditions
    # Look for 'The domain is ...' or 'Domain is ...'
    m = re.search(r"(?:The\s+)?domain\s+is\s+([^\.]+)", t, flags=re.I)
    conds = None
    other_vars = None
    if m:
        dom = m.group(1).strip()
        # Expect something like: h,m>=1 or h, m \geq 1
        # Extract variable list before a comparator
        vm = re.match(r"([A-Za-z_,\s]+)\s*(>=|≥|>)+\s*1", dom)
        if vm:
            vars_part = vm.group(1)
            vars_list = [v.strip() for v in vars_part.split(',') if v.strip()]
            other_vars = "{" + ",".join(vars_list) + "}"
            # Normalize comparator to strict '>' for this project’s convention
            # and reproduce formatting like "h >1 && m > 1"
            pieces = []
            for i, v in enumerate(vars_list):
                if i == 0:
                    pieces.append(f"{v} >1")
                else:
                    pieces.append(f"{v} > 1")
            conds = " && ".join(pieces)
    if not conds or not other_vars:
        # Fallback: scan for simple 'x>1' patterns and build set
        vars_found = sorted(set(re.findall(r"\b([A-Za-z]\w*)\s*>\s*1\b", t)))
        if vars_found:
            other_vars = "{" + ",".join(vars_found) + "}"
            # Default uniform spacing
            conds = " && ".join([f"{v} > 1" for v in vars_found])
        else:
            raise ValueError("Could not parse domain/conditions")

    # 4) Conjectured bound
    m = re.search(r"bounded\s+above\s+by\s+([^\.]+)", t, flags=re.I)
    if not m:
        raise ValueError("Could not parse conjectured bound after 'bounded above by'")
    conj_raw = m.group(1).strip()
    conj = _normalize_wl_funcs(conj_raw)

    return series_to_bound(
        formula=formula,
        conditions=conds,
        summation_index=idx,
        other_variables=other_vars,
        summation_bounds=[lower, upper],
        conjectured_upper_asymptotic_bound=conj,
    )


def parse_series_smart(text: str) -> series_to_bound:
    """Prefer deterministic parse; fall back to LLM if needed."""
    try:
        return parse_series_text(text)
    except Exception:
        return parse_series(text)


def demo_from_prompt() -> None:
    s = (
        """Consider the series: (2*d+1)/(2*h^2*(1+d*(d+1)/(h^2))(1+d*(d+1)/(h^2*m^2))^2), 
        where d is summed from 0 to infinity. The upper bound should be 1+log m^2. The domain is h,m>= 1. """
    )
    obj = parse_series_smart(s)
    print(obj)


# ------------------------
# Shared normalization helpers
# ------------------------


_LOG_FUNS = ("log", "Log", "\\log")
_EXP_FUNS = ("exp", "Exp", "\\exp")


def _normalize_to_wl(expr: str) -> str:
    """Normalize informal or LaTeX math into Mathematica syntax."""

    s = expr
    s = s.replace("\t", " ").replace("\\,", "")
    s = s.replace("×", "*").replace("·", "*").replace("\\times", "*")
    for name in _LOG_FUNS:
        pattern = re.escape(name)
        s = re.sub(rf"{pattern}\\s*([A-Za-z]\\w*)", r"log(\1)", s)
    for name in _EXP_FUNS:
        pattern = re.escape(name)
        s = re.sub(rf"{pattern}\\s*([A-Za-z]\\w*)", r"exp(\1)", s)
    s = re.sub(r"\be\^\(([^)]*)\)", r"exp(\1)", s)
    s = re.sub(r"\be\^([A-Za-z]\\w*)", r"exp(\1)", s)
    s = _productize_simple(s)
    s = re.sub(r"log\s*\((.*?)\)", r"Log[\1]", s)
    s = re.sub(r"exp\s*\((.*?)\)", r"Exp[\1]", s)
    s = re.sub(r"\\log\s*\((.*?)\)", r"Log[\1]", s)
    s = re.sub(r"\\exp\s*\((.*?)\)", r"Exp[\1]", s)
    try:
        return _latex_to_wl(s)
    except Exception:
        return s


# ------------------------
# Inequality parser (LLM)
# ------------------------

def parse_inequality(text: str) -> Tuple[str, str, str, str]:
    """Parse a free-form inequality description into an `inequality` spec.

    Attempts an LLM-based parse first; falls back to deterministic parsing on
    failure. Ensures the output uses Mathematica syntax compatible with the
    downstream CAS workflow.
    """

    try:
        return _llm_parse_inequality(text)
    except Exception:
        return parse_inequality_text(text)


def _ensure_brace_list(value: str) -> str:
    txt = value.strip()
    if not txt or txt == "{}":
        return "{}"
    if txt.lower() == "true":
        return "True"
    if txt.startswith("{") and txt.endswith("}"):
        inner = txt[1:-1].strip()
        if not inner:
            return "{}"
        return "{" + ", ".join([p.strip() for p in inner.split(",") if p.strip()]) + "}"
    parts = [p.strip() for p in txt.split(",") if p.strip()]
    if not parts:
        return "{}"
    return "{" + ", ".join(parts) + "}"


def _llm_parse_inequality(text: str) -> Tuple[str, str, str, str]:
    output_format = '{"variables":"{...}","domain_description":"{...}","lhs":"...","rhs":"..."}'
    prompt = f"""<code_editing_rules>
  <guiding_principles>
    – Be precise and deterministic.
    – Output only compact JSON with fields using Mathematica syntax (Log[], Exp[], Sqrt[], ^, *, /, +, -).
    – Convert any LaTeX (\\sum, \^, subscripts) into Mathematica expressions.
    – Preserve every explicit domain constraint; if none are given, use {{}}.
    – Include every variable appearing in the inequality or domain.
  </guiding_principles>

  <task>
    Read the following description of an inequality. Produce the specification
    required to construct `inequality(variables=..., domain_description=..., lhs=..., rhs=...)`.
    Interpret symbols like «<<» or «≪» as describing that the left-hand side
    should be bounded by the right-hand side (use the same expressions for lhs
    and rhs).
  </task>

  <output_format>
    {output_format}
  </output_format>
</code_editing_rules>

Description:
{text}
"""

    raw = api_call(prompt=prompt)
    spec = _extract_json(raw)

    def _field(name: str) -> str:
        val = spec.get(name, "")
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Missing or invalid field: {name}")
        return val.strip()

    variables_raw = _field("variables")
    domain_raw = _field("domain_description")

    variables = _ensure_brace_list(variables_raw)
    domain = _ensure_brace_list(domain_raw)
    lhs = _normalize_to_wl(_field("lhs"))
    rhs = _normalize_to_wl(_field("rhs"))

    # Derive variables if still empty
    inner = variables[1:-1].strip() if variables.startswith("{") and variables.endswith("}") else variables
    if not inner:
        symbol_set = set(re.findall(r"[A-Za-z]\\w*", ",".join([domain, lhs, rhs])))
        symbol_set -= {"Log", "Exp"}
        names = sorted(symbol_set)
        variables = "{" + ",".join(names) + "}"

    if domain == "{}" and "&&" in domain_raw:
        domain = "{" + ", ".join([p.strip() for p in domain_raw.split("&&") if p.strip()]) + "}"

    return variables, domain, lhs, rhs


def _strip_dollars_all(s: str) -> str:
    return s.replace("$", "").strip()


def _productize_simple(expr: str) -> str:
    """Convert 'x y' to 'x*y' for simple tokens; keep (), [], {} intact."""
    s = expr
    s = re.sub(r"\\s+", " ", s).strip()
    # Insert * between adjacent alphanumerics/underscores
    s = re.sub(r"([A-Za-z0-9_])\\s+([A-Za-z0-9_])", r"\1*\2", s)
    # Insert * between ) and variable/number
    s = re.sub(r"\)\\s*([A-Za-z0-9_])", r")*\1", s)
    # Insert * between variable/number and (
    s = re.sub(r"([A-Za-z0-9_])\\s*\(", r"\1*(", s)
    return s


def parse_inequality_text(text: str) -> Tuple[str, str, str, str]:
    t = _strip_dollars_all(" ".join(text.strip().split()))
    # Pre-normalize common LaTeX/Unicode tokens and escapes
    t = t.replace("\t", " ")              # tabs → space
    t = t.replace("\\,", "")               # LaTeX thin space
    t = t.replace("×", "*")                 # unicode times
    t = t.replace("·", "*")                 # middle dot
    t = t.replace("\\times", "*")         # LaTeX times
    # Split LHS and RHS on delimiter: \ll, <<, or Unicode ≪
    # Accept one or two backslashes to be safe
    m = re.search(r"(.+?)(?:\\\\?ll|<<|≪)\s*(.+)", t, flags=re.UNICODE)
    if not m:
        raise ValueError("Could not find inequality delimiter (\\ll or <<)")
    lhs_raw = m.group(1).strip()
    rhs_and_rest = m.group(2).strip()
    # Bounds/domain extraction
    dom_match = re.search(r"bounds\s*:\s*([^$]+)$|domain\s*(?:is|:)\s*([^$]+)$", rhs_and_rest, flags=re.I)
    if dom_match:
        rhs_raw = rhs_and_rest[:dom_match.start()].strip().rstrip(',')
        dom_text = dom_match.group(1) or dom_match.group(2) or ""
    else:
        # No explicit bounds; default empty domain
        rhs_raw = rhs_and_rest
        dom_text = ""
    lhs = _normalize_to_wl(lhs_raw)
    rhs = _normalize_to_wl(rhs_raw)
    # Build domain conditions and variable list robustly
    var_set = []
    conds = []
    if dom_text:
        dt = dom_text.replace("\\geq", ">=").replace("\\gt", ">").strip()
        tokens = [t.strip() for t in re.split(r"[;,]", dt) if t.strip()]
        for tok in tokens:
            mvar = re.match(r"^([A-Za-z]\\w*)\\s*(>=|>|<=|<|==)\\s*([^\s]+)$", tok)
            if mvar:
                v, op, val = mvar.group(1), mvar.group(2), mvar.group(3).rstrip(".;,")
                conds.append(f"{v}{op}{val}")
                if v not in var_set:
                    var_set.append(v)
        if not conds:
            mgrp = re.match(r"^([A-Za-z_,\\s]+)\\s*(>=|>|<=|<|==)\\s*([^\s]+)$", dt)
            if mgrp:
                names = [v.strip() for v in mgrp.group(1).split(',') if v.strip()]
                op = mgrp.group(2)
                val = mgrp.group(3).rstrip(".;,")
                for v in names:
                    conds.append(f"{v}{op}{val}")
                    if v not in var_set:
                        var_set.append(v)
    if not var_set:
        for v in sorted(set(re.findall(r"[A-Za-z]\\w*", lhs + "," + rhs))):
            if v not in ("Log", "Exp"):
                var_set.append(v)
    variables = "{" + ",".join(var_set) + "}"
    domain_description = "{" + ", ".join(conds) + "}" if conds else "{}"
    return variables, domain_description, lhs, rhs
# ------------------------
# LaTeX parser
# ------------------------

def _strip_dollars(s: str) -> str:
    s = s.strip()
    if s.startswith("$") and s.endswith("$"):
        return s[1:-1]
    return s


def _latex_frac_to_parens(s: str) -> str:
    """Convert all \frac{A}{B} to (A)/(B), handling nesting by scanning."""
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s.startswith("\\frac{", i):
            i += len("\\frac")
            # Expect {A}{B}
            if i < n and s[i] == '{':
                # parse first group
                def read_group(j):
                    assert s[j] == '{'
                    depth = 0
                    j0 = j + 1
                    while j < n:
                        if s[j] == '{':
                            depth += 1
                        elif s[j] == '}':
                            depth -= 1
                            if depth == 0:
                                return s[j0:j], j + 1
                        j += 1
                    raise ValueError('Unbalanced braces in \\frac')
                A, i = read_group(i)
                if i < n and s[i] == '{':
                    B, i = read_group(i)
                else:
                    raise ValueError('Expected second group in \\frac')
                out.append('(' + A + ')/(' + B + ')')
                continue
        out.append(s[i])
        i += 1
    return ''.join(out)


def _latex_frac_to_parens2(s: str) -> str:
    """Alternative implementation using find() to robustly convert nested \frac."""
    i = 0
    out = ''
    while True:
        pos = s.find('\\frac{', i)
        if pos == -1:
            out += s[i:]
            break
        out += s[i:pos]
        j = pos + len('\\frac')
        if j >= len(s) or s[j] != '{':
            out += '\\frac'
            i = j
            continue
        # Read A
        depth = 0
        a_start = j + 1
        k = a_start
        while k < len(s):
            if s[k] == '{':
                depth += 1
            elif s[k] == '}':
                if depth == 0:
                    a_end = k
                    k += 1
                    break
                depth -= 1
            k += 1
        else:
            raise ValueError('Unbalanced braces in \\frac A')
        if k >= len(s) or s[k] != '{':
            raise ValueError('Expected second group in \\frac')
        # Read B
        depth = 0
        b_start = k + 1
        m = b_start
        while m < len(s):
            if s[m] == '{':
                depth += 1
            elif s[m] == '}':
                if depth == 0:
                    b_end = m
                    m += 1
                    break
                depth -= 1
            m += 1
        else:
            raise ValueError('Unbalanced braces in \\frac B')
        A = s[a_start:a_end]
        B = s[b_start:b_end]
        out += f'({A})/({B})'
        i = m
    return out


def _latex_to_wl(expr: str) -> str:
    s = expr
    s = _strip_dollars(s)
    # Remove any stray dollar signs
    s = s.replace("$", "")
    # Heuristic: restore missing backslashes for common commands
    if "\\frac" not in s and "frac{" in s:
        s = s.replace("frac{", "\\frac{")
    if "\\left" not in s and "left" in s:
        s = s.replace("left", "\\left")
    if "\\right" not in s and "right" in s:
        s = s.replace("right", "\\right")
    # Remove \left \right
    s = s.replace("\\left", "").replace("\\right", "")
    # Convert \frac recursively
    s = _latex_frac_to_parens2(s)
    # Functions: \log(...) -> Log[...]
    s = re.sub(r"\\log\s*\((.*?)\)", r"Log[\1]", s)
    s = re.sub(r"\\exp\s*\((.*?)\)", r"Exp[\1]", s)
    s = re.sub(r"\\sqrt\s*\((.*?)\)", r"Sqrt[\1]", s)
    # Power braces ^{...} -> ^(...)
    s = re.sub(r"\^\{([^}]*)\}", r"^(\1)", s)
    # Infinity
    s = s.replace("\\infty", "Infinity")
    # Remove spaces around * and /
    s = re.sub(r"\s+", " ", s).strip()
    # Insert * for common juxtapositions
    # between letter/number/closing bracket and (
    s = re.sub(r"([A-Za-z0-9_\]])\s*\(", r"\1*(", s)
    # between ) and letter/number
    s = re.sub(r"\)\s*([A-Za-z0-9_])", r")*\1", s)
    # between symbol^number and symbol
    s = re.sub(r"([A-Za-z_]\^\d+)\s*([A-Za-z_])", r"\1*\2", s)
    # between adjacent parentheses
    s = s.replace(') (', ')*(')
    s = s.replace(')(', ')*(')
    # Final cleanup of spaces
    s = s.replace(" ", "")
    return s


def parse_series_latex(text: str) -> series_to_bound:
    """Parse a LaTeX inequality with a sum into series_to_bound.

    Accepts common variants such as optional \limits, and optional bounds/domain clause.
    """
    t = " ".join(text.strip().split())

    # Capture sum with optional \limits, limits (with/without braces), summand,
    # and optional bound after \ll. Allow trailing $ before comma/period.
    m = re.search(r"\\sum(?:\s*\\limits)?_\{?([^}]*)\}?\^\{?([^}]*)\}?\s*(.*?)(?:\\ll\s*([^$,]+))?(?:\$?[,\.;]|$)", t)
    if not m:
        raise ValueError("Could not parse LaTeX sum and bound")
    sub = m.group(1)
    sup = m.group(2)
    summand_tex = m.group(3).strip()
    bound_tex = (m.group(4) or "").strip()
    if "\\ll" in summand_tex:
        summand_tex = summand_tex.split("\\ll", 1)[0].strip()

    # Parse subscript like d=0
    mm = re.match(r"\s*([A-Za-z]\\w*)\s*=\s*([^\s]+)\s*", sub)
    if not mm:
        mm = re.match(r"\s*([A-Za-z])\s*=\s*([^\s]+)\s*", sub)
    if not mm:
        raise ValueError("Unsupported subscript; expected d=0 form")
    idx = re.sub(r"\\", "", mm.group(1))
    lower = _latex_to_wl(mm.group(2))

    # Superscript: Infinity
    upper = _latex_to_wl(sup)

    # Convert summand and bound
    formula = _latex_to_wl(summand_tex)
    conj = _latex_to_wl(bound_tex) if bound_tex else "1"

    # Bounds/domains clause: look for 'bounds:' or 'domain:' (optional)
    conds = None
    other_vars = None
    m2 = re.search(r"bounds\s*:\s*(?:\$)?([^$.,;]+)", t, flags=re.I)
    if not m2:
        m2 = re.search(r"(?:the\s+)?domain\s*(?:is|:)\s*\$?([^$]+)\$?", t, flags=re.I)
    if m2:
        dom = m2.group(1)
        dom = dom.replace("$", "")
        dom = dom.strip().rstrip(".;,")
        # Normalize common LaTeX comparators
        dom = dom.replace("\\geq", ">=").replace("\\gt", ">")
        # Extract var-op-value triples like a>=1, h>1, m>=1 (comma-separated)
        triples_raw = re.findall(r"([A-Za-z]\\w*)\s*(>=|>)\s*([^,\s]+)", dom)
        triples = []
        for v, op, val in triples_raw:
            val = val.rstrip(".;,)")
            triples.append((v, op, val))
        if triples:
            vars_list = [v for (v, _, _) in triples]
            other_vars = "{" + ",".join(vars_list) + "}"
            cond_pieces = [f"{v}{op}{val}" for (v, op, val) in triples]
            conds = " && ".join(cond_pieces)
    # Allow empty bounds if nothing was found
    if conds is None or other_vars is None:
        conds = ""
        other_vars = "{}"

    return series_to_bound(
        formula=formula,
        conditions=conds,
        summation_index=idx,
        other_variables=other_vars,
        summation_bounds=[lower, upper],
        conjectured_upper_asymptotic_bound=conj,
    )
    
if __name__=="__main__":
    demo_from_prompt()

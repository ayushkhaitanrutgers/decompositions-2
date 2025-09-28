import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from llm_client import api_call


def _load_env_var(key: str) -> Optional[str]:
    """Resolve `key`, falling back to loading .env-style files if needed."""

    value = os.environ.get(key)
    if value:
        return value

    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
        value = os.environ.get(key)
        if value:
            return value
    except Exception:
        pass

    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(env_path):
        return None

    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :]
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k != key:
                    continue
                v = v.strip().strip('\"\'')
                os.environ.setdefault(k, v)
                return v
    except Exception:
        return None

    return os.environ.get(key)


def _resolve_wolframscript() -> str:
    """Return a usable wolframscript executable path."""
    env_path = os.environ.get("WOLFRAMSCRIPT")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    which_path = shutil.which("wolframscript")
    if which_path:
        return which_path

    for candidate in (
        "/Users/ayushkhaitan/Desktop/Wolfram.app/Contents/MacOS/wolframscript",
        "/Applications/Wolfram.app/Contents/MacOS/wolframscript",
        "/Applications/WolframScript.app/Contents/MacOS/wolframscript",
        "/usr/local/bin/wolframscript",
        "/opt/homebrew/bin/wolframscript",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    raise FileNotFoundError(
        "wolframscript not found. Set $WOLFRAMSCRIPT or ensure it's on PATH."
    )


WOLFRAM_API_URL: Optional[str] = _load_env_var("WOLFRAM_API_URL")
_USE_WOLFRAM_CLOUD = bool(WOLFRAM_API_URL)
_WOLFRAM_TIMEOUT = float(os.environ.get("WOLFRAM_TIMEOUT", "120"))

WOLFRAMSCRIPT: Optional[str]
if _USE_WOLFRAM_CLOUD:
    WOLFRAMSCRIPT = None
else:
    WOLFRAMSCRIPT = _resolve_wolframscript()


def _clean_env() -> dict:
    """Return a sanitized environment for launching wolframscript."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD")}
    env["PATH"] = os.environ.get("PATH", "")
    return env


def _cloud_eval(code: str) -> str:
    if not WOLFRAM_API_URL:
        raise RuntimeError("WOLFRAM_API_URL is not configured for cloud execution.")

    data = urllib.parse.urlencode({"code": code}).encode("utf-8")
    request = urllib.request.Request(
        WOLFRAM_API_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=_WOLFRAM_TIMEOUT) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset).strip()


def _normalize_expr(expr: str) -> str:
    return expr.replace("exp[", "Exp[").replace("log[", "Log[")


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        key = item
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def _domain_parts(domain: str) -> List[str]:
    stripped = domain.strip()
    if stripped.lower() == "true":
        return []
    if stripped.startswith("{") and stripped.endswith("}"):
        stripped = stripped[1:-1]
    return [p.strip() for p in stripped.split(",") if p.strip()]


def _as_mathematica_list(text: str, allow_true: bool = False) -> str:
    stripped = text.strip()
    if not stripped:
        return "{}"
    if allow_true and stripped.lower() == "true":
        return "True"
    if stripped.lower() == "true":
        return "{}"
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    parts = [p.strip() for p in stripped.split(",") if p.strip()]
    if not parts:
        return "{}"
    return "{" + ", ".join(parts) + "}"


def _parse_subdomains(raw: str) -> List[str]:
    if not raw:
        return []
    stripped = raw.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped.strip("`").split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
        stripped = stripped.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    if not stripped:
        return []

    pieces = []
    current = []
    depth = 0
    for ch in stripped:
        if ch == "," and depth == 0:
            segment = "".join(current).strip()
            if segment:
                pieces.append(segment)
            current = []
            continue
        if ch in "({[":
            depth += 1
        elif ch in ")}]" and depth > 0:
            depth -= 1
        current.append(ch)
    if current:
        segment = "".join(current).strip()
        if segment:
            pieces.append(segment)
    return pieces


def wl_eval(expr: str, form: str = "InputForm") -> str:
    """Evaluate a Wolfram Language expression and return the textual output."""
    wrapped = f'ToString[({expr}), {form}]'
    if _USE_WOLFRAM_CLOUD:
        print("[wolfram] Using Wolfram Cloud endpoint", flush=True)
        return _cloud_eval(wrapped)
    if not WOLFRAMSCRIPT:
        raise RuntimeError("wolframscript binary unavailable for local execution")
    print("[wolfram] Using local wolframscript", WOLFRAMSCRIPT, flush=True)
    cmd = [WOLFRAMSCRIPT, "-code", wrapped]
    return subprocess.check_output(cmd, text=True, env=_clean_env()).strip()


def wl_eval_json(expr: str):
    wrapped = f'ExportString[({expr}), "JSON"]'
    if _USE_WOLFRAM_CLOUD:
        print("[wolfram] Using Wolfram Cloud endpoint", flush=True)
        data = _cloud_eval(wrapped)
    else:
        if not WOLFRAMSCRIPT:
            raise RuntimeError("wolframscript binary unavailable for local execution")
        print("[wolfram] Using local wolframscript", WOLFRAMSCRIPT, flush=True)
        cmd = [WOLFRAMSCRIPT, "-code", wrapped]
        data = subprocess.check_output(cmd, text=True, env=_clean_env()).strip()
    return json.loads(data)


def wl_bool(expr: str) -> bool:
    out = wl_eval(expr, form="InputForm")
    if out == "True":
        return True
    if out == "False":
        return False
    raise ValueError(f"Unexpected output: {out!r}")


def attempt_proof(vars_str: str, conds_str: str, lhs: str, rhs: str) -> str:
    vars_wl = _as_mathematica_list(vars_str, allow_true=True)
    conds_wl = _as_mathematica_list(conds_str)
    lhs_wl = _normalize_expr(lhs)
    rhs_wl = _normalize_expr(rhs)

    for c in range(1):
        raw = wl_eval(
            f"""
Block[{{witnessBigO, witnessBigOAny}}, 
 witnessBigO[vars_, conds_, lhs_, rhs_, c_] := 
  Module[{{S}}, 
   S = If[conds === {{}} || conds === {{}}, True, And @@ conds];
   Resolve[ForAll[vars, Implies[S, lhs <= 10^c rhs]], Reals]];
 (*True if ANY permutation of vars makes the predicate resolve to True\
*)witnessBigOAny[vars_, conds_, lhs_, rhs_, c_] := 
  AnyTrue[Permutations[vars], 
   TrueQ@witnessBigO[#, conds, lhs, rhs, c] &];
  witnessBigOAny[{vars_wl}, {conds_wl}, {lhs_wl}, {rhs_wl}, {c}]
]
            """
        )
        if raw == "True":
            return "It is proved"
        if raw == "False":
            return "This is False"

    return "Status unknown. Try a different setup"


@dataclass
class inequality:
    variables: str
    domain_description: str
    lhs: str
    rhs: str


def try_and_prove(problem: "inequality") -> str:
    base_parts = _domain_parts(problem.domain_description)
    base_clause = " && ".join(base_parts) if base_parts else "True"
    domain_for_prompt = ", ".join(base_parts) if base_parts else "True"
    output_format = (
        f"[{base_clause} && subdomain1, {base_clause} && subdomain2, ...]"
        if base_parts
        else "[subdomain1, subdomain2, ...]"
    )

    prompt = f"""<code_editing_rules>
  <guiding_principles>
    – Be precise, avoid conflicting instructions
    – Use natural subdomains so the inequality proof is trivial
    – Minimize the number of subdomains while covering the whole domain
    – Output only Mathematica-parsable inequalities using <, >, <=, >=, Log[], Exp[]
  </guiding_principles>

  <task>
    Given domain: {domain_for_prompt}
    Inequality: {problem.lhs} <= {problem.rhs}
    Return a list of subdomains whose union is the domain and on which the proof is trivial.
    Find the simplest subdomains. Prioritize simplicity. 
  </task>

  <output_format>
    {output_format}
  </output_format>
</code_editing_rules>
"""

    try:
        llm_raw = api_call(prompt=prompt)
    except Exception as exc:
        print(f"Failed to obtain domain decomposition: {exc}")
        return "Status unknown. Try a different setup"

    if not llm_raw:
        print("LLM returned no decomposition.")
        return "Status unknown. Try a different setup"

    llm_raw_clean = llm_raw.strip()
    print(llm_raw_clean)

    subdomains = _parse_subdomains(llm_raw_clean)
    if not subdomains:
        print("Could not parse subdomains from LLM output.")
        return "Status unknown. Try a different setup"

    success = True

    for idx, entry in enumerate(subdomains, start=1):
        tokens = [tok.strip() for tok in entry.split("&&") if tok.strip()]
        cond_list = _dedupe_preserve(base_parts + tokens) if base_parts else _dedupe_preserve(tokens)
        conds_str = "{" + ", ".join(cond_list) + "}"
        result = attempt_proof(problem.variables, conds_str, problem.lhs, problem.rhs)
        print(f"Subdomain {idx}: {entry}")
        print(f"  Result: {result}")
        if result != "It is proved":
            success = False

    if success:
        print("Proved everywhere")
        return "It is proved"

    print("Not proved on at least one subdomain")
    return "Status unknown. Try a different setup"


__all__ = [
    "inequality",
    "try_and_prove",
    "attempt_proof",
    "wl_eval",
    "wl_eval_json",
    "wl_bool",
]

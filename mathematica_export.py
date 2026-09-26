import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from prover import (
    ProofResult,
    check_coverage,
    prove_bigO,
    split_conditions,
    split_variables,
)


def api_call(prompt: str, **kwargs):
    """Ask the LLM (imported lazily so the prover works without google-genai)."""
    from llm_client import api_call as _api_call

    return _api_call(prompt=prompt, **kwargs)


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

    import glob

    candidates = [
        "/Users/ayushkhaitan/Desktop/Wolfram.app/Contents/MacOS/wolframscript",
        "/Applications/Wolfram.app/Contents/MacOS/wolframscript",
        "/Applications/WolframScript.app/Contents/MacOS/wolframscript",
        "/usr/local/bin/wolframscript",
        "/opt/homebrew/bin/wolframscript",
    ]
    # Linux clusters (Princeton Della keeps Mathematica under /usr/licensed).
    for pattern in (
        "/usr/licensed/Mathematica-*/Executables/wolframscript",
        "/usr/local/Wolfram/Mathematica/*/Executables/wolframscript",
        "/opt/Wolfram/Mathematica/*/Executables/wolframscript",
    ):
        candidates.extend(sorted(glob.glob(pattern), reverse=True))
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    raise FileNotFoundError(
        "wolframscript not found. Set $WOLFRAMSCRIPT or ensure it's on PATH "
        "(on Della: module load mathematica/14.2.0)."
    )


WOLFRAM_API_URL: Optional[str] = _load_env_var("WOLFRAM_API_URL")
_USE_WOLFRAM_CLOUD = bool(WOLFRAM_API_URL)
_WOLFRAM_TIMEOUT = float(os.environ.get("WOLFRAM_TIMEOUT", "120"))

WOLFRAMSCRIPT: Optional[str]
if _USE_WOLFRAM_CLOUD:
    WOLFRAMSCRIPT = None
else:
    try:
        WOLFRAMSCRIPT = _resolve_wolframscript()
    except FileNotFoundError:
        # The robust prover (prover.py) locates a kernel by itself; only the
        # low-level wl_eval helpers below need wolframscript.
        WOLFRAMSCRIPT = None


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
        return response.read().decode(response.headers.get_content_charset() or "utf-8").strip()


def _normalize_expr(expr: str) -> str:
    from prover import to_wolfram

    return to_wolfram(expr)


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
    """Split a domain description into its conditions.

    Commas inside brackets (e.g. ``Max[x, y] > 1``) and ``&&`` are handled;
    the old version split on every comma."""
    return split_conditions(domain)


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


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            inner = "\n".join(lines[1:-1]).strip()
            return inner
    return stripped


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
    return _run_with_timeout(cmd, _WOLFRAM_TIMEOUT).strip()


def _run_with_timeout(cmd: List[str], timeout: float) -> str:
    """Run a wolframscript command; on timeout kill the whole process group so
    that the WolframKernel child does not linger."""
    import signal

    proc = subprocess.Popen(
        cmd, text=True, env=_clean_env(), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        proc.wait()
        raise TimeoutError(f"wolframscript exceeded {timeout:.0f} s")
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, out)
    return out


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
        data = _run_with_timeout(cmd, _WOLFRAM_TIMEOUT).strip()

    if data.strip() == "ERROR":
        print("Not proved", flush=True)
        # Return a sentinel structure so downstream code can continue gracefully.
        return {"Logs": ["Wolfram returned ERROR"], "Result": False}

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        print("Raw response from Wolfram:", repr(data), flush=True)
        raise




def wl_bool(expr: str) -> bool:
    out = wl_eval(expr, form="InputForm")
    if out == "True":
        return True
    if out == "False":
        return False
    raise ValueError(f"Unexpected output: {out!r}")


_LAST_RESULT: Optional[ProofResult] = None


def attempt_proof(vars_str: str, conds_str: str, lhs: str, rhs: str, **options) -> str:
    """Try to prove lhs <= C * rhs on the domain described by conds_str.

    Returns one of the historical strings:
        "It is proved"   -- Mathematica proved it for some constant C
                            (details, including C, in last_result())
        "This is False"  -- Mathematica proved that no constant works
        "Status unknown. Try a different setup"

    The old version tried a single call to Resolve[] with C = 1 and answered
    "This is False" whenever that call returned False, which is wrong when a
    bigger constant is needed.  Now the work is done by prover.py: input
    cleaning, sanity checks, a ladder of constants, several methods and
    rewrites, time limits, and a genuine disproof before saying "False".
    """
    global _LAST_RESULT
    result = prove_bigO(lhs, rhs, vars_str, conds_str, **options)
    _LAST_RESULT = result
    print(result, flush=True)
    return result.legacy_string()


def last_result() -> Optional[ProofResult]:
    """Full details of the most recent attempt_proof / try_and_prove call."""
    return _LAST_RESULT


@dataclass
class inequality:
    variables: str
    domain_description: str
    lhs: str
    rhs: str
    parameters: str = ""      # symbols the constant may depend on, e.g. "eps"
    eventually: str = ""      # variables that only need to be large, e.g. "x" (x -> Infinity)
    absolute: bool = False    # prove |lhs| <= C |rhs| instead of lhs <= C rhs


def try_and_prove(problem: "inequality", *, direct_time_limit: float = 120.0,
                  time_limit: float = 30.0, total_limit: float = 600.0) -> str:
    """Prove problem.lhs << problem.rhs on problem.domain_description.

    1. Try the whole domain directly with the robust prover.  Many statements
       that used to need an LLM decomposition (e.g. Young's inequality) are
       proved this way, and a false statement is reported as false here.
    2. Otherwise ask the LLM for subdomains, check with Mathematica that the
       subdomains really cover the domain, and prove the statement on each.
       "Proved everywhere" is printed only if every piece is proved AND the
       coverage check passed.  The old version never checked coverage.
    """
    global _LAST_RESULT
    base_parts = _domain_parts(problem.domain_description)
    base_clause = " && ".join(base_parts) if base_parts else "True"
    domain_for_prompt = ", ".join(base_parts) if base_parts else "True"

    print(f"Direct attempt on the whole domain ({domain_for_prompt})", flush=True)
    direct = prove_bigO(problem.lhs, problem.rhs, problem.variables, problem.domain_description,
                        parameters=getattr(problem, "parameters", ""),
                        eventually=getattr(problem, "eventually", ""),
                        absolute=getattr(problem, "absolute", False),
                        time_limit=time_limit, total_limit=direct_time_limit)
    _LAST_RESULT = direct
    print(direct, flush=True)
    if direct.status == "proved":
        print("Proved everywhere")
        return "It is proved"
    if direct.status == "disproved":
        print("This is False")
        return "This is False"
    if direct.status in ("ill-posed", "error"):
        print("Not proved: the problem statement needs fixing (see message above).")
        return "Status unknown. Try a different setup"

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
    – If no such decomposition exists, output exactly: Subdomains not found
  </guiding_principles>

  <task>
    Given domain: {domain_for_prompt}
    Inequality: {problem.lhs} <= {problem.rhs}
    Return a list of subdomains whose union is the domain and on which the proof is trivial.
    Find the simplest subdomains. Prioritize simplicity. 
  </task>

  <output_format>
    {output_format}
    or exactly: Subdomains not found
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
    llm_raw_inner = _strip_code_fences(llm_raw_clean)
    no_subdomains = llm_raw_inner.lower() == "subdomains not found" or llm_raw_inner == "[]"
    if no_subdomains:
        print("Subdomains not found")
        return "Subdomains not found"

    print(llm_raw_clean)

    subdomains = _parse_subdomains(llm_raw_clean)
    if not subdomains:
        print("Could not parse subdomains from LLM output.")
        return "Status unknown. Try a different setup"

    return prove_on_subdomains(problem, subdomains, base_parts, time_limit=time_limit,
                               total_limit=total_limit)


def prove_on_subdomains(problem: "inequality", subdomains: List[str], base_parts: Optional[List[str]] = None,
                        *, time_limit: float = 30.0, total_limit: float = 600.0) -> str:
    """Prove the statement on each subdomain and check that they cover the domain."""
    global _LAST_RESULT
    if base_parts is None:
        base_parts = _domain_parts(problem.domain_description)

    params = getattr(problem, "parameters", "") or ""
    all_symbols = problem.variables + (", " + params if params.strip() else "")
    coverage = check_coverage(all_symbols, problem.domain_description, subdomains,
                              time_limit=time_limit, total_limit=total_limit)
    print(coverage, flush=True)
    if coverage.covered == "no":
        print("The proposed subdomains do not cover the domain; a proof on them would prove nothing.")
    for idx in coverage.empty_subdomains:
        print(f"  Note: subdomain {idx} ({subdomains[idx - 1]}) is empty; it is skipped.")

    success = True
    constants: List[str] = []
    for idx, entry in enumerate(subdomains, start=1):
        if idx in coverage.empty_subdomains:
            continue
        tokens = split_conditions(entry)
        cond_list = _dedupe_preserve(base_parts + tokens) if base_parts else _dedupe_preserve(tokens)
        conds_str = "{" + ", ".join(cond_list) + "}"
        print(f"Subdomain {idx}: {entry}")
        result = attempt_proof(problem.variables, conds_str, problem.lhs, problem.rhs,
                               parameters=params, eventually=getattr(problem, "eventually", ""),
                               absolute=getattr(problem, "absolute", False),
                               time_limit=time_limit, total_limit=total_limit)
        print(f"  Result: {result}")
        if result != "It is proved":
            success = False
            if result == "This is False":
                print("This is False")
                return "This is False"
        elif _LAST_RESULT is not None and _LAST_RESULT.constant_exact:
            constants.append(_LAST_RESULT.constant_exact)

    if success and coverage.covered == "yes":
        if constants:
            print(f"Constants per subdomain: {constants}; the largest works everywhere.")
        print("Proved everywhere")
        return "It is proved"

    if success and coverage.covered == "unknown":
        print("Proved on every subdomain, but Mathematica could not confirm that the subdomains "
              "cover the whole domain, so this is NOT a complete proof.")
        return "Status unknown. Try a different setup"

    print("Not proved on at least one subdomain")
    return "Status unknown. Try a different setup"


__all__ = [
    "inequality",
    "try_and_prove",
    "prove_on_subdomains",
    "attempt_proof",
    "last_result",
    "wl_eval",
    "wl_eval_json",
    "wl_bool",
]

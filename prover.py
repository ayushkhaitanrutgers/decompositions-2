"""prover.py -- a robust Python front end to Mathematica for asymptotic inequalities.

The question it answers: is there a constant C > 0 such that

    lhs <= C * rhs      at every point of the domain?

(In papers this is written  lhs << rhs  or  lhs = O(rhs).)

Typical use::

    from prover import prove_bigO
    r = prove_bigO("x*y", "x*Log[x] + Exp[y]", "x, y", "x > 1, y > 0")
    print(r)            # one-paragraph human summary
    r.status            # "proved" | "disproved" | "unknown" | "ill-posed" | "error"
    r.constant          # a constant that works, e.g. 2.0 (None if unknown)

What makes it robust compared with a single call to Resolve[]:

* Input is cleaned first: ``**`` -> ``^``, ``log(x)``/``ln(x)`` -> ``Log[x]``,
  ``sqrt(x)`` -> ``Sqrt[x]``, ``<=`` written as ``≤``, and so on.  Variables
  whose names Mathematica reserves (C, D, E, I, K, N, O, ...) are renamed.
* Before proving anything it checks that the domain is non-empty, that both
  sides are real numbers on the domain, that the right side is >= 0 (otherwise
  "lhs << rhs" has no meaning) and it warns if the left side can be negative.
* It tries a ladder of constants (1, 2, 4, 10, ..., 10^6), several Mathematica
  methods (Resolve, Reduce, Simplify, FullSimplify, MaxValue, a symbolic
  constant) and several equivalent rewrites of the statement (x -> E^u to
  remove logarithms, x -> t^q to remove roots, y -> g + s to remove a lower
  bound y >= g, case-splitting Max/Min/Abs).  A "False" from any rewrite is a
  sound "False" for the original statement.
* If nothing proves it, it looks for a disproof (ratio unbounded, right side
  zero where the left is positive, "for every C there is a bad point") and,
  failing that, at least a counterexample for the largest constant tried.
* Every Mathematica call has a time limit.  If the kernel ignores the limit
  (this happens with Reduce on a symbolic constant), the whole kernel process
  group is killed and restarted; finished steps are replayed from a log, so
  nothing is repeated and the run continues with the next step.

Only the standard library is needed on the Python side.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

__all__ = [
    "ProofResult",
    "CoverageResult",
    "prove_bigO",
    "check_coverage",
    "to_wolfram",
    "split_conditions",
    "split_variables",
    "find_kernel",
    "KernelNotFound",
]

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "prover.wl")

DEFAULT_CONSTANTS: Tuple[int, ...] = (1, 2, 4, 10, 100, 1000, 10 ** 6)
DEFAULT_TIME_LIMIT = 30.0     # seconds per single Mathematica call
DEFAULT_TOTAL_LIMIT = 600.0   # seconds for the whole question
KILL_GRACE = 15.0             # extra seconds before a hard kill of an over-running call
MAX_RESTARTS = 12

# Names Mathematica reserves that people like to use as variables.
RESERVED = {
    "C": "cC", "D": "dD", "E": "eE", "I": "iI", "K": "kK", "N": "nN", "O": "oO",
    "Pi": "pi_", "Gamma": "gam", "Beta": "bet", "Zeta": "zet",
}

# Function names people write with parentheses, mapped to Mathematica heads.
FUNCTION_NAMES = {
    "log": "Log", "ln": "Log", "exp": "Exp", "sqrt": "Sqrt", "abs": "Abs",
    "max": "Max", "min": "Min", "sin": "Sin", "cos": "Cos", "tan": "Tan",
    "sinh": "Sinh", "cosh": "Cosh", "tanh": "Tanh", "arctan": "ArcTan",
    "atan": "ArcTan", "arcsin": "ArcSin", "arccos": "ArcCos", "floor": "Floor",
    "ceil": "Ceiling", "ceiling": "Ceiling", "sign": "Sign", "power": "Power",
    "gamma": "Gamma", "zeta": "Zeta",
}


class KernelNotFound(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Finding a Mathematica kernel
# ---------------------------------------------------------------------------

def _executable(path: Optional[str]) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def find_kernel() -> Tuple[str, str]:
    """Return (path, mode).  mode is "kernel" (wolfram / WolframKernel / math,
    starts in about 2 s) or "wolframscript" (slower start, about 10 s).

    Order: $WOLFRAM_KERNEL, $WOLFRAMSCRIPT, PATH, then well-known install
    locations on Linux clusters and Macs (newest version first).
    """
    env_k = os.environ.get("WOLFRAM_KERNEL")
    if _executable(env_k):
        return env_k, "kernel"
    env_s = os.environ.get("WOLFRAMSCRIPT")
    if _executable(env_s):
        return env_s, "wolframscript"
    for name in ("wolfram", "WolframKernel", "math"):
        p = shutil.which(name)
        if p:
            return p, "kernel"
    p = shutil.which("wolframscript")
    if p:
        return p, "wolframscript"
    patterns = [
        ("/usr/licensed/Mathematica-*/bin/wolfram", "kernel"),
        ("/usr/licensed/Mathematica-*/Executables/wolframscript", "wolframscript"),
        ("/usr/local/Wolfram/Mathematica/*/Executables/wolfram", "kernel"),
        ("/usr/local/Wolfram/Mathematica/*/Executables/wolframscript", "wolframscript"),
        ("/opt/Wolfram/Mathematica/*/Executables/wolfram", "kernel"),
        ("/opt/wolfram/*/Executables/wolfram", "kernel"),
        ("/Applications/Wolfram.app/Contents/MacOS/WolframKernel", "kernel"),
        ("/Applications/Mathematica.app/Contents/MacOS/WolframKernel", "kernel"),
        (os.path.expanduser("~/Desktop/Wolfram.app/Contents/MacOS/WolframKernel"), "kernel"),
        ("/Applications/Wolfram.app/Contents/MacOS/wolframscript", "wolframscript"),
        ("/Applications/Mathematica.app/Contents/MacOS/wolframscript", "wolframscript"),
        ("/Applications/WolframScript.app/Contents/MacOS/wolframscript", "wolframscript"),
        ("/usr/local/bin/wolframscript", "wolframscript"),
        ("/opt/homebrew/bin/wolframscript", "wolframscript"),
    ]
    for pat, mode in patterns:
        for cand in sorted(glob.glob(pat), reverse=True):
            if _executable(cand):
                return cand, mode
    raise KernelNotFound(
        "No Mathematica kernel found. Set WOLFRAM_KERNEL=/path/to/wolfram (or "
        "WOLFRAMSCRIPT=/path/to/wolframscript), or on Della run "
        "`module load mathematica/14.2.0` first."
    )


# ---------------------------------------------------------------------------
# Cleaning up user input
# ---------------------------------------------------------------------------

_UNICODE = {
    "≤": "<=", "≥": ">=", "≠": "!=", "×": "*", "−": "-",
    "⋅": "*", "·": "*", "∞": "Infinity", "π": "Pi",
    "∧": "&&", "∨": "||",
}


def _convert_calls(s: str) -> str:
    """Turn name(...) into Head[...] for known function names, and lowercase
    name[...] into Head[...].  Parentheses are matched properly."""
    out: List[str] = []
    stack: List[str] = []  # for each open bracket, what closes it
    i = 0
    n = len(s)
    while i < n:
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s[i:])
        if m:
            word = m.group(0)
            j = i + len(word)
            k = j
            while k < n and s[k] == " ":
                k += 1
            opener = s[k] if k < n else ""
            head = FUNCTION_NAMES.get(word.lower()) if word.lower() in FUNCTION_NAMES else None
            if head and opener in "([" and not (word[0].isupper() and opener == "["):
                out.append(head + "[")
                stack.append("]")
                i = k + 1
                continue
            # "foo(x)" with a multi-letter name is a function call in the
            # writer's mind, not a product.  Pass it on as foo[x] so the
            # kernel can say clearly that foo is not a known function.
            if opener == "(" and len(word) >= 2 and word[0].isalpha() and word.isalnum():
                out.append(word + "[")
                stack.append("]")
                i = k + 1
                continue
            out.append(word)
            i = j
            continue
        ch = s[i]
        if ch in "([{":
            out.append(ch)
            stack.append({"(": ")", "[": "]", "{": "}"}[ch])
        elif ch in ")]}":
            want = stack.pop() if stack else ch
            out.append(want)
        else:
            out.append(ch)
        i += 1
    while stack:
        out.append(stack.pop())
    return "".join(out)


def to_wolfram(expr: str) -> str:
    """Normalise a user-written expression into Mathematica syntax.

    >>> to_wolfram("x**2 + log(x) + sqrt(y)")
    'x^2 + Log[x] + Sqrt[y]'
    >>> to_wolfram("exp[y] ≤ 3")
    'Exp[y] <= 3'
    """
    s = expr.strip()
    for a, b in _UNICODE.items():
        s = s.replace(a, b)
    s = s.replace("**", "^")
    s = _convert_calls(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_top_level(text: str, seps: Sequence[str]) -> List[str]:
    """Split on any of `seps` that occur outside brackets."""
    parts: List[str] = []
    cur: List[str] = []
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if depth == 0:
            hit = None
            for sep in seps:
                if text.startswith(sep, i):
                    hit = sep
                    break
            if hit:
                parts.append("".join(cur).strip())
                cur = []
                i += len(hit)
                continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def split_variables(variables: Union[str, Sequence[str]]) -> List[str]:
    """'x, y' | '{x,y}' | 'x y' | ['x','y'] -> ['x', 'y']"""
    if not isinstance(variables, str):
        items = [str(v) for v in variables]
    else:
        s = variables.strip()
        if s.lower() in ("", "true", "{}", "none"):
            return []
        if s.startswith("{") and s.endswith("}"):
            s = s[1:-1]
        items = re.split(r"[,\s]+", s)
    return [to_wolfram(v) for v in items if v.strip()]


def split_conditions(conditions: Union[str, Sequence[str], None]) -> List[str]:
    """'{y>0, x>1}' | 'y>0 && x>1' | ['y>0','x>1'] | 'True' -> ['y>0', 'x>1']"""
    if conditions is None:
        return []
    if not isinstance(conditions, str):
        raw: List[str] = []
        for c in conditions:
            raw.extend(split_conditions(str(c)))
        return raw
    s = conditions.strip()
    if s.lower() in ("", "true", "{}", "none"):
        return []
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    parts = _split_top_level(s, [",", "&&"])
    out: List[str] = []
    for p in parts:
        p = to_wolfram(p)
        if p.lower() == "true":
            continue
        out.append(p)
    return out


def _rename_reserved(names: List[str], texts: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """Rename reserved variable names consistently in the variables and all texts."""
    warnings: List[str] = []
    new_names = list(names)
    new_texts = list(texts)
    for idx, name in enumerate(names):
        clean = name
        if "_" in clean:
            clean = clean.replace("_", "")
            warnings.append(f"Variable {name} renamed to {clean} (underscores mean patterns in Mathematica).")
        if clean in RESERVED:
            new = RESERVED[clean]
            warnings.append(f"Variable {clean} renamed to {new} ({clean} is reserved by Mathematica).")
            clean = new
        if clean != name:
            pat = re.compile(r"(?<![A-Za-z0-9_`])" + re.escape(name) + r"(?![A-Za-z0-9_`])")
            new_texts = [pat.sub(clean, t) for t in new_texts]
            new_names[idx] = clean
    return new_names, new_texts, warnings


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class ProofResult:
    status: str                       # proved | disproved | unknown | ill-posed | error
    message: str = ""
    constant: Optional[float] = None  # a constant that works (numeric)
    constant_exact: Optional[str] = None
    best_constant: Optional[str] = None
    method: Optional[str] = None
    formulation: Optional[str] = None
    formulation_detail: Optional[str] = None
    statement: Optional[str] = None
    witness: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    log: List[Dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    restarts: int = 0
    inputs: Dict[str, Any] = field(default_factory=dict)

    @property
    def proved(self) -> bool:
        return self.status == "proved"

    def legacy_string(self) -> str:
        """The three strings the original code returned."""
        if self.status == "proved":
            return "It is proved"
        if self.status == "disproved":
            return "This is False"
        return "Status unknown. Try a different setup"

    def __str__(self) -> str:
        lines = [f"[{self.status.upper()}] {self.message}"]
        if self.status == "proved":
            if self.constant_exact is not None:
                extra = f" (best possible: {self.best_constant})" if self.best_constant else ""
                lines.append(f"  constant C = {self.constant_exact}{extra}")
            lines.append(f"  method: {self.method}; formulation: {self.formulation}"
                         + (f" [{self.formulation_detail}]" if self.formulation_detail else ""))
        if self.witness:
            lines.append(f"  counterexample: {self.witness}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        lines.append(f"  ({len(self.log)} Mathematica steps, {self.seconds:.1f} s"
                     + (f", {self.restarts} kernel restart(s)" if self.restarts else "") + ")")
        return "\n".join(lines)


@dataclass
class CoverageResult:
    covered: str                      # yes | no | unknown
    message: str = ""
    uncovered_point: Optional[str] = None
    empty_subdomains: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    log: List[Dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    def __str__(self) -> str:
        lines = [f"[coverage: {self.covered}] {self.message}"]
        if self.empty_subdomains:
            lines.append(f"  empty subdomains (1-based): {self.empty_subdomains}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Running the kernel with a watchdog
# ---------------------------------------------------------------------------

def _read_log(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _condense_log(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn start/done pairs into one record per step (killed steps included)."""
    steps: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for e in events:
        label = e.get("label")
        if not label:
            continue
        if e.get("event") == "start":
            if label not in steps:
                steps[label] = {"label": label, "result": "killed", "seconds": None}
                order.append(label)
        elif e.get("event") == "done":
            if label not in steps:
                order.append(label)
            steps[label] = {"label": label, "result": e.get("result"), "seconds": e.get("seconds"),
                            "value": (e.get("value") or "")[:300]}
    return [steps[l] for l in order]


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _run_job(spec: Dict[str, Any], workdir: str, time_limit: float, total_limit: float,
             verbose: bool = False) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], float, int, str]:
    """Run prover.wl on `spec`, restarting the kernel after hard hangs.

    Returns (result_dict_or_None, condensed_log, seconds, restarts, note)."""
    kernel, mode = find_kernel()
    spec_path = os.path.join(workdir, "spec.json")
    log_path = os.path.join(workdir, "log.jsonl")
    result_path = os.path.join(workdir, "result.json")
    out_path = os.path.join(workdir, "kernel_output.txt")
    spec = dict(spec)
    spec["log"] = log_path
    spec["result"] = result_path
    spec["time_limit"] = time_limit
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    if os.path.exists(result_path):
        os.remove(result_path)

    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD")}
    env["ASYM_SPEC"] = spec_path
    if mode == "kernel":
        cmd = [kernel, "-noprompt", "-script", DRIVER]
    else:
        cmd = [kernel, "-file", DRIVER]

    t_start = time.time()
    restarts = 0
    note = ""
    hard_limit = time_limit + KILL_GRACE
    while True:
        if verbose:
            print(f"[prover] starting kernel ({'restart %d' % restarts if restarts else 'first run'})", flush=True)
        with open(out_path, "a", encoding="utf-8") as out_fh:
            proc = subprocess.Popen(cmd, env=env, stdout=out_fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True, cwd=workdir)
            seen_starts = 0
            seen_events = -1
            step_started_at = time.time()
            last_event_at = time.time()
            killed_for_hang = False
            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                now = time.time()
                events = _read_log(log_path)
                n_starts = sum(1 for e in events if e.get("event") == "start")
                if n_starts != seen_starts:
                    seen_starts = n_starts
                    step_started_at = now
                n_events = len(events)
                if n_events != seen_events:
                    seen_events = n_events
                    last_event_at = now
                open_step = events and events[-1].get("event") == "start"
                idle_too_long = now - last_event_at > 2 * hard_limit + 60
                if (open_step and now - step_started_at > hard_limit) or idle_too_long:
                    if verbose:
                        print(f"[prover] step {events[-1].get('label')} ignored its time limit; killing kernel", flush=True)
                    _kill(proc)
                    killed_for_hang = True
                    break
                if now - t_start > total_limit:
                    _kill(proc)
                    note = f"Stopped after the total time limit of {total_limit:.0f} s."
                    break
                time.sleep(0.25)
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as fh:
                try:
                    result = json.load(fh)
                except json.JSONDecodeError:
                    result = None
            return result, _condense_log(_read_log(log_path)), time.time() - t_start, restarts, note
        if note:
            break
        if killed_for_hang and restarts < MAX_RESTARTS:
            restarts += 1
            continue
        if killed_for_hang:
            note = "Too many kernel restarts."
        else:
            tail = ""
            try:
                with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
                    tail = fh.read()[-1500:]
            except OSError:
                pass
            note = "The Mathematica kernel exited without an answer. Kernel output:\n" + tail
        break
    return None, _condense_log(_read_log(log_path)), time.time() - t_start, restarts, note


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _prepare(lhs: str, rhs: str, variables, conditions, parameters=None, eventually=None):
    lhs_w = to_wolfram(lhs)
    rhs_w = to_wolfram(rhs)
    vars_w = split_variables(variables)
    params_w = split_variables(parameters) if parameters else []
    ev_w = split_variables(eventually) if eventually else []
    conds_w = split_conditions(conditions)
    names, texts, warnings = _rename_reserved(vars_w + params_w + ev_w, [lhs_w, rhs_w] + conds_w)
    n1, n2 = len(vars_w), len(vars_w) + len(params_w)
    vars_w, params_w, ev_w = names[:n1], names[n1:n2], names[n2:]
    lhs_w, rhs_w, conds_w = texts[0], texts[1], texts[2:]
    return lhs_w, rhs_w, vars_w, params_w, ev_w, conds_w, warnings


def prove_bigO(lhs: str, rhs: str, variables: Union[str, Sequence[str]],
               conditions: Union[str, Sequence[str], None] = None, *,
               parameters: Union[str, Sequence[str], None] = None,
               eventually: Union[str, Sequence[str], None] = None,
               absolute: bool = False,
               constants: Sequence[Union[int, float]] = DEFAULT_CONSTANTS,
               time_limit: float = DEFAULT_TIME_LIMIT,
               total_limit: float = DEFAULT_TOTAL_LIMIT,
               workdir: Optional[str] = None,
               verbose: bool = False) -> ProofResult:
    """Decide whether lhs <= C * rhs for some constant C > 0 on the domain.

    lhs, rhs      expressions (Mathematica or ordinary syntax: x**2, log(x), ...)
    variables     "x, y" or ["x", "y"]
    conditions    "x > 1, y > 0" or "x > 1 && y > 0" or a list; "True"/None = no condition
    parameters    symbols the constant C may depend on, e.g. "eps" (their
                  conditions go in `conditions` too).  First a constant
                  independent of them is sought; failing that, one that
                  depends on them, e.g. C = 1/eps.
    eventually    variables that only need to be large: "x" means the estimate
                  must hold for x >= T for some threshold T (as x -> Infinity).
                  Thresholds 1, 10, 100, 1000, 10^6 are tried.
    absolute      prove |lhs| <= C |rhs| instead of lhs <= C rhs
    constants     the constants tried, in order
    time_limit    seconds allowed for one Mathematica call
    total_limit   seconds allowed for the whole question
    workdir       keep the spec/log/result files here (default: a temp dir, deleted)
    """
    t0 = time.time()
    lhs_w, rhs_w, vars_w, params_w, ev_w, conds_w, warnings = _prepare(
        lhs, rhs, variables, conditions, parameters, eventually)
    inputs = {"lhs": lhs_w, "rhs": rhs_w, "variables": vars_w, "parameters": params_w,
              "eventually": ev_w, "conditions": conds_w, "absolute": absolute}
    spec = {"job": "prove", "lhs": lhs_w, "rhs": rhs_w, "vars": vars_w, "params": params_w,
            "eventually": ev_w, "conds": conds_w,
            "absolute": bool(absolute), "constants": [int(c) if float(c).is_integer() else float(c) for c in constants]}

    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory(prefix="asymprover_")
        workdir = tmp.name
    os.makedirs(workdir, exist_ok=True)
    try:
        try:
            result, log, secs, restarts, note = _run_job(spec, workdir, time_limit, total_limit, verbose)
        except KernelNotFound as exc:
            return ProofResult(status="error", message=str(exc), warnings=warnings, inputs=inputs,
                               seconds=time.time() - t0)
    finally:
        if tmp is not None:
            tmp.cleanup()

    if result is None:
        # Build the best answer we can from the log.
        msg = note or "No answer from Mathematica."
        return ProofResult(status="unknown", message=msg, warnings=warnings, log=log,
                           seconds=secs, restarts=restarts, inputs=inputs)

    pr = ProofResult(
        status=result.get("status", "error"),
        message=result.get("message", ""),
        constant=result.get("constant_numeric"),
        constant_exact=result.get("constant"),
        best_constant=result.get("best_constant"),
        method=result.get("method"),
        formulation=result.get("formulation"),
        formulation_detail=result.get("formulation_detail"),
        statement=result.get("statement"),
        witness=result.get("witness"),
        warnings=warnings + list(result.get("warnings") or []),
        log=log, seconds=secs, restarts=restarts, inputs=inputs,
    )
    if note:
        pr.warnings.append(note)
    return pr


def check_coverage(variables: Union[str, Sequence[str]],
                   conditions: Union[str, Sequence[str], None],
                   subdomains: Sequence[str], *,
                   time_limit: float = DEFAULT_TIME_LIMIT,
                   total_limit: float = DEFAULT_TOTAL_LIMIT,
                   workdir: Optional[str] = None,
                   verbose: bool = False) -> CoverageResult:
    """Do the subdomains (each a condition string) together cover the domain?"""
    vars_w = split_variables(variables)
    conds_w = split_conditions(conditions)
    subs_w = [" && ".join(split_conditions(s)) or "True" for s in subdomains]
    vars_w, texts, warnings = _rename_reserved(vars_w, conds_w + subs_w)
    conds_w, subs_w = texts[: len(conds_w)], texts[len(conds_w):]
    spec = {"job": "coverage", "vars": vars_w, "conds": conds_w, "subdomains": subs_w}

    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory(prefix="asymcover_")
        workdir = tmp.name
    os.makedirs(workdir, exist_ok=True)
    try:
        try:
            result, log, secs, restarts, note = _run_job(spec, workdir, time_limit, total_limit, verbose)
        except KernelNotFound as exc:
            return CoverageResult(covered="unknown", message=str(exc), warnings=warnings)
    finally:
        if tmp is not None:
            tmp.cleanup()

    if result is None:
        return CoverageResult(covered="unknown", message=note or "No answer from Mathematica.",
                              warnings=warnings, log=log, seconds=secs)
    if result.get("status") == "error" or result.get("status") == "ill-posed":
        return CoverageResult(covered="unknown", message=result.get("message", ""),
                              warnings=warnings + list(result.get("warnings") or []), log=log, seconds=secs)
    return CoverageResult(
        covered=result.get("covered", "unknown"),
        message=result.get("message", ""),
        uncovered_point=result.get("uncovered_point"),
        empty_subdomains=list(result.get("empty_subdomains") or []),
        warnings=warnings + list(result.get("warnings") or []),
        log=log, seconds=secs,
    )


# ---------------------------------------------------------------------------
# Command line:  python prover.py "x*y" "x*Log[x]+Exp[y]" "x,y" "x>1, y>0"
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Prove lhs << rhs (lhs <= C rhs for some C > 0) with Mathematica.")
    ap.add_argument("lhs")
    ap.add_argument("rhs")
    ap.add_argument("variables", help='e.g. "x, y"')
    ap.add_argument("conditions", nargs="?", default="", help='e.g. "x > 1, y > 0"')
    ap.add_argument("--absolute", action="store_true", help="prove |lhs| <= C |rhs|")
    ap.add_argument("-p", "--parameters", default="", help='symbols the constant may depend on, e.g. "eps"')
    ap.add_argument("-e", "--eventually", default="", help='variables that only need to be large, e.g. "x"')
    ap.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT)
    ap.add_argument("--total-limit", type=float, default=DEFAULT_TOTAL_LIMIT)
    ap.add_argument("--workdir", default=None, help="keep the Mathematica log/result files here")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    r = prove_bigO(args.lhs, args.rhs, args.variables, args.conditions, absolute=args.absolute,
                   parameters=args.parameters, eventually=args.eventually,
                   time_limit=args.time_limit, total_limit=args.total_limit, workdir=args.workdir,
                   verbose=args.verbose)
    if args.json:
        print(json.dumps(r.__dict__, indent=2, default=str))
    else:
        print(r)
    return 0 if r.status in ("proved", "disproved") else 1


if __name__ == "__main__":
    raise SystemExit(main())

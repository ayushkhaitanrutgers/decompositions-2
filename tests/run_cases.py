"""Run every case in cases.py and print a table.  Usage:
    python tests/run_cases.py            # all
    python tests/run_cases.py Young      # cases whose name contains 'Young'
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prover import prove_bigO, check_coverage
from cases import CASES, COVERAGE_CASES, PARAM_CASES

TIME_LIMIT = float(os.environ.get("CASE_TIME_LIMIT", "20"))
TOTAL_LIMIT = float(os.environ.get("CASE_TOTAL_LIMIT", "240"))


def check(r, expected, extra):
    ok = r.status in (expected if isinstance(expected, tuple) else (expected,))
    notes = []
    if "constant_ge" in extra and r.status == "proved":
        if r.constant is None or r.constant < extra["constant_ge"] - 1e-9:
            ok = False; notes.append(f"constant {r.constant} < {extra['constant_ge']}")
    if "warning_contains" in extra:
        if not any(extra["warning_contains"] in w for w in r.warnings):
            ok = False; notes.append(f"no warning containing {extra['warning_contains']!r}")
    if "message_contains" in extra and extra["message_contains"].lower() not in r.message.lower():
        ok = False; notes.append(f"message lacks {extra['message_contains']!r}")
    if extra.get("witness_or_disproved") and r.status == "unknown" and not r.witness:
        ok = False; notes.append("no counterexample reported")
    if "constant_contains" in extra and r.status == "proved":
        if extra["constant_contains"] not in str(r.constant_exact):
            ok = False; notes.append(f"constant {r.constant_exact} lacks {extra['constant_contains']!r}")
    return ok, notes


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    fails = 0
    t_all = time.time()
    for name, kw, expected, extra in CASES + PARAM_CASES:
        if pattern and pattern.lower() not in name.lower():
            continue
        r = prove_bigO(time_limit=TIME_LIMIT, total_limit=TOTAL_LIMIT, **kw)
        ok, notes = check(r, expected, extra)
        fails += (not ok)
        cst = f" C={r.constant_exact}" if r.constant_exact else ""
        how = f" via {r.method}/{r.formulation}" if r.method else ""
        print(f"{'PASS' if ok else 'FAIL'}  {name:34s} {r.status:10s}{cst}{how}  ({r.seconds:.0f}s, {len(r.log)} steps"
              + (f", {r.restarts} restarts" if r.restarts else "") + ")")
        if not ok:
            print("      expected", expected, "|", "; ".join(notes))
            print("      message:", r.message)
            for w in r.warnings:
                print("      warning:", w)
        sys.stdout.flush()
    for name, kw, expected, extra in COVERAGE_CASES:
        if pattern and pattern.lower() not in name.lower():
            continue
        c = check_coverage(time_limit=TIME_LIMIT, total_limit=TOTAL_LIMIT, **kw)
        ok = c.covered == expected and c.empty_subdomains == extra.get("empty", [])
        fails += (not ok)
        print(f"{'PASS' if ok else 'FAIL'}  coverage: {name:24s} {c.covered:8s} empty={c.empty_subdomains} ({c.seconds:.0f}s)")
        if not ok:
            print("      ", c.message)
        sys.stdout.flush()
    print(f"\n{fails} failure(s); {time.time() - t_all:.0f} s total")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

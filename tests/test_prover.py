"""pytest wrapper around cases.py plus unit tests that need no Mathematica.
Run:  pytest tests/ -q          (needs a Mathematica kernel for the slow cases)
      pytest tests/ -q -m "not kernel"   for the pure-Python tests only
"""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prover import to_wolfram, split_conditions, split_variables, _rename_reserved, prove_bigO, check_coverage, find_kernel, KernelNotFound
from cases import CASES, COVERAGE_CASES, PARAM_CASES
from run_cases import check, TIME_LIMIT, TOTAL_LIMIT


def test_to_wolfram():
    assert to_wolfram("x**2 + log(x) + sqrt(y)") == "x^2 + Log[x] + Sqrt[y]"
    assert to_wolfram("exp[y] ≤ 3") == "Exp[y] <= 3"
    assert to_wolfram("max(x, min(y,z))") == "Max[x, Min[y,z]]"
    assert to_wolfram("Log[x]") == "Log[x]"
    assert to_wolfram("foo(x)") == "foo[x]"        # unknown name(...) is passed on as a call
    assert to_wolfram("x(y+1)") == "x(y+1)"        # a single letter before ( is a product


def test_split():
    assert split_conditions("{y>0, x>1}") == ["y>0", "x>1"]
    assert split_conditions("x>0 && (y>0 || z>0), w>0") == ["x>0", "(y>0 || z>0)", "w>0"]
    assert split_conditions("True") == []
    assert split_variables("{x,y}") == ["x", "y"]
    assert split_variables("x y") == ["x", "y"]


def test_rename_reserved():
    names, texts, warns = _rename_reserved(["E", "x"], ["E*x + Exp[E]", "E>0"])
    assert names == ["eE", "x"] and texts == ["eE*x + Exp[eE]", "eE>0"] and warns


def _have_kernel():
    try:
        find_kernel(); return True
    except KernelNotFound:
        return False


kernel = pytest.mark.skipif(not _have_kernel(), reason="no Mathematica kernel")


@kernel
@pytest.mark.kernel
@pytest.mark.parametrize("name,kw,expected,extra", CASES + PARAM_CASES, ids=[c[0] for c in CASES + PARAM_CASES])
def test_prove(name, kw, expected, extra):
    r = prove_bigO(time_limit=TIME_LIMIT, total_limit=TOTAL_LIMIT, **kw)
    ok, notes = check(r, expected, extra)
    assert ok, f"{r.status}: {r.message} | {notes}"


@kernel
@pytest.mark.kernel
@pytest.mark.parametrize("name,kw,expected,extra", COVERAGE_CASES, ids=[c[0] for c in COVERAGE_CASES])
def test_coverage(name, kw, expected, extra):
    c = check_coverage(time_limit=TIME_LIMIT, total_limit=TOTAL_LIMIT, **kw)
    assert c.covered == expected, c.message
    assert c.empty_subdomains == extra.get("empty", [])


@kernel
@pytest.mark.kernel
def test_watchdog_restarts_a_hung_kernel(monkeypatch):
    # Make one step hang for ever; the watchdog must kill the kernel, restart it,
    # skip that step and still reach the right answer.
    monkeypatch.setenv("ASYM_TEST_HANG", "prove|C=1|original|Resolve")
    r = prove_bigO("2*x", "x", "x", "x>1", time_limit=3, total_limit=120)
    assert r.restarts >= 1
    assert r.status == "proved" and r.constant >= 2
    assert any(s["label"] == "prove|C=1|original|Resolve" and s["result"] == "killed" for s in r.log)

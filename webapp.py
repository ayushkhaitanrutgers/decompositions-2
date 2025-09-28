import os
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from experiments import parse_series_smart, parse_inequality
from series_summation import series_to_bound


PROJECT_ROOT = Path(__file__).resolve().parent


def _auth_or_401(token_header: Optional[str]):
    required = os.environ.get("WEB_TOKEN")
    if required:
        if not token_header or token_header != required:
            raise HTTPException(status_code=401, detail="Unauthorized")


def run_series(series: series_to_bound) -> str:
    """Invoke the CLI path `decomp series <name>` by creating a temporary
    examples module containing the given series object.

    This ensures the web portal exercises the same code path as the CLI.
    """
    name = "web_series"
    # Build a temporary examples.py that defines the object
    py = (
        "from series_summation import series_to_bound\n"
        f"{name} = series_to_bound(\n"
        f"    formula={series.formula!r},\n"
        f"    conditions={series.conditions!r},\n"
        f"    summation_index={series.summation_index!r},\n"
        f"    other_variables={series.other_variables!r},\n"
        f"    summation_bounds={series.summation_bounds!r},\n"
        f"    conjectured_upper_asymptotic_bound={series.conjectured_upper_asymptotic_bound!r}\n"
        ")\n"
    )

    with tempfile.TemporaryDirectory() as td:
        ex_path = os.path.join(td, "examples.py")
        with open(ex_path, "w", encoding="utf-8") as f:
            f.write(py)

        # Prepare environment: put temp dir first on PYTHONPATH so `import examples` hits ours
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        path_parts = [td, str(PROJECT_ROOT)]
        if existing:
            path_parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(path_parts)

        # Call the CLI entry via Python to avoid reliance on console-script resolution
        code = (
            f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
            f"sys.path.insert(0, {repr(td)}); "
            "sys.argv=['decomp','series','web_series']; "
            "import cli; cli.main()"
        )

        import subprocess as sp
        proc = sp.run(
            [sys.executable, "-c", code],
            env=env,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Execution failed: {proc.stderr.strip()}")
        return (proc.stderr or "") + (proc.stdout or "")


def run_inequality(vars_s: str, domain_s: str, lhs: str, rhs: str) -> str:
    """Create a temporary inequality object and run decomp prove <name>."""
    name = "web_inequality"
    # If variables list is empty, derive from domain and expressions
    vs = (vars_s or "").strip()
    if vs == "{}" or not vs:
        import re
        cand = []
        for v in re.findall(r"([A-Za-z]\\w*)\\s*(?:>=|>|<=|<|==)", domain_s or ""):
            if v not in cand:
                cand.append(v)
        if not cand:
            for v in re.findall(r"[A-Za-z]\\w*", (lhs or "") + "," + (rhs or "")):
                if v not in ("Log", "Exp") and v not in cand:
                    cand.append(v)
        vars_s = "{" + ",".join(cand) + "}" if cand else "{}"
    py = (
        "from mathematica_export import inequality\n"
        f"{name} = inequality(\n"
        f"    variables={vars_s!r},\n"
        f"    domain_description={domain_s!r},\n"
        f"    lhs={lhs!r},\n"
        f"    rhs={rhs!r}\n"
        ")\n"
    )
    with tempfile.TemporaryDirectory() as td:
        ex_path = os.path.join(td, "examples.py")
        with open(ex_path, "w", encoding="utf-8") as f:
            f.write(py)
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        path_parts = [td, str(PROJECT_ROOT)]
        if existing:
            path_parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(path_parts)
        code = (
            f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
            f"sys.path.insert(0, {repr(td)}); "
            "sys.argv=['decomp','prove','web_inequality']; "
            "import cli; cli.main()"
        )
        import subprocess as sp
        proc = sp.run(
            [sys.executable, "-c", code],
            env=env,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Execution failed: {proc.stderr.strip()}")
        return (proc.stderr or "") + (proc.stdout or "")


app = FastAPI(title="Decomp Web")


INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Decomp – Series Parser</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
      textarea { width: 100%; height: 160px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; padding: .75rem; }
      .row { margin: .75rem 0; }
      button { padding: .6rem 1rem; }
      pre { background:#f7f7f8; padding: 1rem; overflow:auto; }
      .muted { color:#666 }
      label { display:inline-block; margin-right: 1rem; }
    </style>
  </head>
  <body>
    <h1>Decomp – Proof Runner</h1>
    <p>Paste a LaTeX/text description. Click run — we auto-detect series vs. inequality.</p>

    <div class="row">
      <textarea id="text" placeholder="e.g. $\\sum_{d=0}^{\\infty} \\frac{2d+1}{2h^2(1+\\frac{d(d+1)}{h^2})(1+\\frac{d(d+1)}{h^2 m^2})^2} \\ll 1+\\log(m^2)$, bounds: $h,m\\geq 1$"></textarea>
    </div>
    <div class="row">
      <label><input type="radio" name="kind" value="series" checked /> Series</label>
      <label><input type="radio" name="kind" value="inequality" /> Inequality</label>
    </div>
    
    <div class="row">
      <input id="wolfram" placeholder="WOLFRAMSCRIPT path (optional)" style="width: 60%" />
    </div>
    <div class="row">
      <input id="token" placeholder="X-Auth-Token (optional if server requires)" style="width: 60%" />
    </div>
    <div class="row">
      <button onclick="run()">Run decomp</button>
    </div>
    <div class="row">
      <h3>Parsed object</h3>
      <pre id="parsed" class="muted">(none)</pre>
      <h3>Run output</h3>
      <pre id="output">(none)</pre>
    </div>

    <script>
      async function run(){
        const text = document.getElementById('text').value;
        const kind = [...document.getElementsByName('kind')].find(r => r.checked).value;
        const wolfram = document.getElementById('wolfram').value.trim();
        const token = document.getElementById('token').value.trim();
        document.getElementById('parsed').textContent = '(running...)';
        document.getElementById('output').textContent = '';
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['X-Auth-Token'] = token;
        const res = await fetch('/api/series', {
          method: 'POST',
          headers,
          body: JSON.stringify({ text, kind, wolframscript: wolfram || null })
        });
        const data = await res.json();
        if (!res.ok){
          document.getElementById('parsed').textContent = 'Error: ' + (data.detail || res.statusText);
          return;
        }
        document.getElementById('parsed').textContent = data.parsed_repr || JSON.stringify(data.parsed, null, 2);
        document.getElementById('output').textContent = data.output;
      }
    </script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)


class SeriesRequest(BaseModel):
    text: str = ""
    mode: str = "latex"  # 'latex' | 'auto' | 'by_name'
    cmd: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[str] = None   # 'series' | 'inequality'
    wolframscript: Optional[str] = None


@app.post("/api/series")
def api_series(req: SeriesRequest, x_auth_token: Optional[str] = Header(default=None, alias="X-Auth-Token")):
    _auth_or_401(x_auth_token)
    if req.wolframscript:
        os.environ["WOLFRAMSCRIPT"] = req.wolframscript

    # Run by example name (supports inequalities)
    if req.mode == "by_name":
        if not req.cmd or not req.name:
            raise HTTPException(status_code=400, detail="Provide cmd ('series'|'prove'|'solve') and name (e.g., series_1 or inequality_1)")
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        path_parts = [str(PROJECT_ROOT)]
        if existing:
            path_parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(path_parts)
        code = (
            f"import sys; sys.path.insert(0, {repr(str(PROJECT_ROOT))}); "
            f"sys.argv=['decomp',{repr(req.cmd)},{repr(req.name)}]; "
            "import cli; cli.main()"
        )
        import subprocess as sp
        proc = sp.run(
            [sys.executable, "-c", code],
            text=True,
            capture_output=True,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Execution failed: {proc.stderr.strip()}")
        return JSONResponse({
            "parsed": None,
            "parsed_repr": None,
            "output": (proc.stderr or "") + (proc.stdout or ""),
        })

    text = (req.text or "").strip()
    if req.kind in ("series", "inequality"):
        is_series = (req.kind == "series")
    else:
        is_series = ("\\sum" in text) or ("Sum[" in text)
    if is_series:
        try:
            series = parse_series_smart(text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Series parse failed: {e}")

        parsed_repr = (
            "series_to_bound(\n"
            f"    formula=\"{series.formula}\",\n"
            f"    conditions=\"{series.conditions}\",\n"
            f"    summation_index=\"{series.summation_index}\",\n"
            f"    other_variables=\"{series.other_variables}\",\n"
            f"    summation_bounds={series.summation_bounds},\n"
            f"    conjectured_upper_asymptotic_bound=\"{series.conjectured_upper_asymptotic_bound}\"\n"
            ")"
        )
        try:
            output = run_series(series)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Execution failed: {e}")
        return JSONResponse({
            "parsed": {
                "formula": series.formula,
                "conditions": series.conditions,
                "summation_index": series.summation_index,
                "other_variables": series.other_variables,
                "summation_bounds": series.summation_bounds,
                "conjectured_upper_asymptotic_bound": series.conjectured_upper_asymptotic_bound,
            },
            "parsed_repr": parsed_repr,
            "output": output,
        })
    else:
        try:
            vars_s, domain_s, lhs, rhs = parse_inequality(text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Inequality parse failed: {e}")
        parsed_repr = (
            "inequality(\n"
            f"    variables=\"{vars_s}\",\n"
            f"    domain_description=\"{domain_s}\",\n"
            f"    lhs=\"{lhs}\",\n"
            f"    rhs=\"{rhs}\"\n"
            ")"
        )
        try:
            output = run_inequality(vars_s, domain_s, lhs, rhs)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Execution failed: {e}")
        return JSONResponse({
            "parsed": {
                "variables": vars_s,
                "domain_description": domain_s,
                "lhs": lhs,
                "rhs": rhs,
            },
            "parsed_repr": parsed_repr,
            "output": output,
        })


def main():
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("webapp:app", host=host, port=port, reload=False)

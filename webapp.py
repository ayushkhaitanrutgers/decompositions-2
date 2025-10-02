import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from experiments import parse_series_smart, parse_inequality
from series_summation import series_to_bound
from mathematica_export import inequality


PROJECT_ROOT = Path(__file__).resolve().parent


@lru_cache()
def _collect_examples() -> List[Dict[str, Any]]:
    """Inspect examples.py and build a metadata list for the frontend."""
    try:
        import examples
    except Exception as exc:  # pragma: no cover - surfaced to client
        raise RuntimeError(f"Failed to import examples.py: {exc}")

    entries: List[Dict[str, Any]] = []
    for name, obj in sorted(vars(examples).items()):
        if name.startswith("_"):
            continue
        if isinstance(obj, series_to_bound):
            bounds = obj.summation_bounds if isinstance(obj.summation_bounds, list) else ["?", "?"]
            summary = (
                f"Sum_{obj.summation_index}={bounds[0]}..{bounds[1]} of {obj.formula}"
                if len(bounds) == 2
                else f"Series in {obj.summation_index}: {obj.formula}"
            )
            entries.append(
                {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "type": "series",
                    "cmd": "series",
                    "summary": summary,
                    "details": {
                        "conditions": obj.conditions,
                        "other_variables": obj.other_variables,
                        "conjectured_upper_asymptotic_bound": obj.conjectured_upper_asymptotic_bound,
                    },
                }
            )
        elif isinstance(obj, inequality):
            summary = f"Prove {obj.lhs} <= {obj.rhs}"
            if getattr(obj, "domain_description", ""):
                summary += f" for {obj.domain_description}"
            entries.append(
                {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "type": "inequality",
                    "cmd": "prove",
                    "summary": summary,
                    "details": {
                        "variables": getattr(obj, "variables", ""),
                        "domain_description": getattr(obj, "domain_description", ""),
                    },
                }
            )
    return entries


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
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Decomp Workspace</title>
    <style>
      :root {
        --bg: #f1f4f9;
        --panel: #ffffff;
        --accent: #1f6feb;
        --accent-soft: rgba(31, 111, 235, 0.12);
        --border: #d6dde8;
        --text: #161b26;
        --muted: #626b7b;
        --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
        --sans: "Inter", "Segoe UI", Roboto, system-ui, sans-serif;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        min-height: 100vh;
        background: linear-gradient(135deg, #e3eaf6 0%, #fdfdfd 45%, #e9f1ff 100%);
        color: var(--text);
        font-family: var(--sans);
      }
      .page {
        display: flex;
        flex-direction: row;
        gap: 1.5rem;
        max-width: 1200px;
        margin: 0 auto;
        padding: 2rem 1.5rem 3rem;
      }
      .sidebar {
        width: 320px;
        background: var(--panel);
        border-radius: 1rem;
        box-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
        padding: 1.5rem;
        display: flex;
        flex-direction: column;
        max-height: calc(100vh - 4rem);
        position: sticky;
        top: 2rem;
      }
      .sidebar h2 {
        margin: 0;
        font-size: 1.1rem;
        letter-spacing: 0.02em;
      }
      .sidebar-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1rem;
        gap: 0.5rem;
      }
      .main {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 1.5rem;
      }
      .card {
        background: var(--panel);
        border-radius: 1rem;
        padding: 1.75rem;
        box-shadow: 0 12px 40px rgba(15, 23, 42, 0.12);
      }
      .card h2 {
        margin: 0 0 0.75rem 0;
        font-size: 1.35rem;
      }
      .subtitle {
        margin: 0;
        color: var(--muted);
      }
      textarea {
        width: 100%;
        min-height: 180px;
        padding: 1rem;
        border-radius: 0.75rem;
        border: 1px solid var(--border);
        font-family: var(--mono);
        font-size: 0.95rem;
        background: #f9fbff;
        resize: vertical;
      }
      textarea:focus, input:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 3px var(--accent-soft);
      }
      .field {
        margin-bottom: 1rem;
      }
      .field label {
        display: block;
        font-weight: 600;
        margin-bottom: 0.4rem;
      }
      .inline-inputs {
        display: flex;
        gap: 1rem;
        flex-wrap: wrap;
      }
      .inline-inputs input {
        flex: 1;
        min-width: 220px;
        padding: 0.75rem 0.9rem;
        border-radius: 0.65rem;
        border: 1px solid var(--border);
        font-size: 0.95rem;
      }
      .radio-group {
        display: inline-flex;
        gap: 0.75rem;
        background: #f4f6fc;
        border-radius: 999px;
        padding: 0.35rem;
      }
      .radio-group label {
        display: flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.45rem 0.9rem;
        border-radius: 999px;
        cursor: pointer;
        transition: background 0.2s ease, color 0.2s ease;
      }
      .radio-group input {
        display: none;
      }
      .radio-group label.active {
        background: var(--accent);
        color: #fff;
      }
      .actions {
        display: flex;
        justify-content: flex-end;
        gap: 0.75rem;
        margin-top: 1rem;
      }
      button.primary {
        background: var(--accent);
        border: none;
        color: #fff;
        padding: 0.75rem 1.5rem;
        border-radius: 0.75rem;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
      }
      button.primary:hover {
        transform: translateY(-1px);
        box-shadow: 0 10px 18px rgba(31, 111, 235, 0.18);
      }
      button.ghost {
        background: transparent;
        border: 1px solid var(--border);
        border-radius: 0.65rem;
        padding: 0.55rem 1rem;
        font-weight: 600;
        color: var(--text);
        cursor: pointer;
        transition: background 0.2s ease;
      }
      button.ghost:hover {
        background: rgba(15, 23, 42, 0.06);
      }
      .example-list {
        overflow-y: auto;
        padding-right: 0.25rem;
        flex: 1;
      }
      .example-card {
        border: 1px solid transparent;
        border-radius: 0.9rem;
        padding: 1rem;
        margin-bottom: 0.75rem;
        background: #f7f9ff;
        text-align: left;
        cursor: pointer;
        transition: transform 0.15s ease, background 0.2s ease, border 0.2s ease;
      }
      .example-card:last-child {
        margin-bottom: 0;
      }
      .example-card:hover {
        transform: translateY(-2px);
        background: #fff;
        border-color: rgba(31, 111, 235, 0.2);
      }
      .example-card.active {
        border-color: var(--accent);
        background: #fff;
        box-shadow: 0 10px 24px rgba(31, 111, 235, 0.18);
      }
      .example-type {
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--muted);
        margin-bottom: 0.25rem;
      }
      .example-title {
        font-weight: 600;
        font-size: 1rem;
        margin-bottom: 0.25rem;
      }
      .example-summary {
        font-size: 0.85rem;
        color: var(--muted);
        line-height: 1.4;
      }
      pre {
        margin: 0;
        padding: 1rem;
        background: #0f172a;
        color: #e2e8f0;
        border-radius: 0.75rem;
        font-family: var(--mono);
        font-size: 0.9rem;
        overflow-x: auto;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .muted {
        color: rgba(255, 255, 255, 0.65);
      }
      .results-header {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        margin-bottom: 0.6rem;
      }
      .status-label {
        font-size: 0.85rem;
        color: var(--muted);
      }
      .status-label.running {
        color: var(--accent);
      }
      .status-label.error {
        color: #d93025;
      }
      .empty-state {
        padding: 1.2rem;
        border-radius: 0.75rem;
        background: #eef2fb;
        color: var(--muted);
        font-size: 0.9rem;
      }
      @media (max-width: 1080px) {
        .page { flex-direction: column; }
        .sidebar { position: static; width: 100%; max-height: none; }
        .main { width: 100%; }
      }
    </style>
  </head>
  <body>
    <div class="page">
      <aside class="sidebar">
        <div class="sidebar-header">
          <h2>Example Library</h2>
          <button class="ghost" id="reload-examples" type="button">Reload</button>
        </div>
        <div id="examples" class="example-list">
          <div class="empty-state" id="examples-empty">Examples load on demand. Click reload if needed.</div>
        </div>
      </aside>
      <main class="main">
        <section class="card">
          <h1>Decomp Workspace</h1>
          <p class="subtitle">Run decompositions on curated examples or your own expressions.</p>
        </section>
        <section class="card">
          <h2>Manual Input</h2>
          <div class="field">
            <label for="text">Problem statement</label>
            <textarea id="text" placeholder="Describe a series or inequality to decompose..."></textarea>
          </div>
          <div class="field">
            <span style="font-weight:600; display:block; margin-bottom:0.4rem;">Mode</span>
            <div class="radio-group" id="kind-group">
              <label data-kind="series" class="active"><input type="radio" name="kind" value="series" checked />Series</label>
              <label data-kind="inequality"><input type="radio" name="kind" value="inequality" />Inequality</label>
            </div>
          </div>
          <div class="field">
            <label>Runtime options</label>
            <div class="inline-inputs">
              <input id="wolfram" placeholder="WOLFRAMSCRIPT path (optional)" />
              <input id="token" placeholder="X-Auth-Token (if required)" />
            </div>
          </div>
          <div class="actions">
            <button class="primary" id="run-input" type="button">Run input</button>
          </div>
        </section>
        <section class="card">
          <div class="results-header">
            <h2>Parsed Object</h2>
            <span class="status-label" id="parsed-status">Idle</span>
          </div>
          <pre id="parsed" class="muted">(none)</pre>
        </section>
        <section class="card">
          <div class="results-header">
            <h2>Run Output</h2>
            <span class="status-label" id="output-status">Idle</span>
          </div>
          <pre id="output">(none)</pre>
        </section>
      </main>
    </div>

    <script>
      const state = {
        selectedExample: null,
      };

      function escapeHtml(value) {
        return String(value || '')
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      function getKind() {
        const checked = document.querySelector('input[name="kind"]:checked');
        return checked ? checked.value : 'series';
      }

      function setKind(kind) {
        const radios = document.querySelectorAll('.radio-group label');
        radios.forEach(label => {
          const input = label.querySelector('input');
          const isMatch = input.value === kind;
          input.checked = isMatch;
          label.classList.toggle('active', isMatch);
        });
      }

      function updateStatus(target, message, type) {
        const el = document.getElementById(target);
        el.textContent = message;
        el.classList.remove('running', 'error');
        if (type) {
          el.classList.add(type);
        }
      }

      function clearSelectionHighlight() {
        document.querySelectorAll('.example-card.active').forEach(card => card.classList.remove('active'));
      }

      function highlightCard(card) {
        clearSelectionHighlight();
        if (card) {
          card.classList.add('active');
        }
      }

      function renderExamples(items) {
        const container = document.getElementById('examples');
        const empty = document.getElementById('examples-empty');
        container.innerHTML = '';
        if (!items.length) {
          empty.textContent = 'No examples detected in examples.py.';
          empty.style.display = 'block';
          return;
        }
        empty.style.display = 'none';
        items.forEach(item => {
          const card = document.createElement('button');
          card.type = 'button';
          card.className = 'example-card';
          card.innerHTML = `
            <div class="example-type">${escapeHtml(item.type)}</div>
            <div class="example-title">${escapeHtml(item.label)}</div>
            <div class="example-summary">${escapeHtml(item.summary)}</div>
          `;
          card.addEventListener('click', () => runExample(item, card));
          container.appendChild(card);
        });
      }

      async function loadExamples() {
        const token = document.getElementById('token').value.trim();
        const headers = {};
        if (token) headers['X-Auth-Token'] = token;
        try {
          const res = await fetch('/api/examples', { headers });
          if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            document.getElementById('examples-empty').textContent = 'Failed to load examples: ' + (data.detail || res.statusText);
            document.getElementById('examples-empty').style.display = 'block';
            return;
          }
          const payload = await res.json();
          renderExamples(payload.examples || []);
        } catch (err) {
          document.getElementById('examples-empty').textContent = 'Failed to load examples: ' + err;
          document.getElementById('examples-empty').style.display = 'block';
        }
      }

      async function executeRequest(body, label) {
        const headers = { 'Content-Type': 'application/json' };
        const token = document.getElementById('token').value.trim();
        const wolfram = document.getElementById('wolfram').value.trim();
        if (token) headers['X-Auth-Token'] = token;
        if (body && !body.wolframscript) {
          body.wolframscript = wolfram || null;
        }

        updateStatus('parsed-status', 'Running...', 'running');
        updateStatus('output-status', 'Running...', 'running');
        document.getElementById('parsed').textContent = '(running...)';
        document.getElementById('output').textContent = '';

        try {
          const res = await fetch('/api/series', {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
          });
          const data = await res.json();
          if (!res.ok) {
            updateStatus('parsed-status', 'Error', 'error');
            updateStatus('output-status', 'Error', 'error');
            document.getElementById('parsed').textContent = 'Error: ' + (data.detail || res.statusText);
            document.getElementById('output').textContent = '';
            return;
          }
          updateStatus('parsed-status', label ? `Ran ${label}` : 'Completed');
          updateStatus('output-status', 'Completed');
          document.getElementById('parsed').textContent = data.parsed_repr || JSON.stringify(data.parsed, null, 2);
          document.getElementById('output').textContent = data.output || '(no output)';
        } catch (err) {
          updateStatus('parsed-status', 'Error', 'error');
          updateStatus('output-status', 'Error', 'error');
          document.getElementById('parsed').textContent = 'Request failed: ' + err;
          document.getElementById('output').textContent = '';
        }
      }

      async function runExample(example, cardEl) {
        state.selectedExample = example.name;
        highlightCard(cardEl);
        setKind(example.type);
        const label = example.label || example.name;
        await executeRequest({ mode: 'by_name', cmd: example.cmd, name: example.name, kind: example.type }, label);
      }

      async function runManual() {
        clearSelectionHighlight();
        const text = document.getElementById('text').value;
        const kind = getKind();
        state.selectedExample = null;
        await executeRequest({ text, kind }, 'Manual input');
      }

      document.getElementById('run-input').addEventListener('click', runManual);
      document.getElementById('reload-examples').addEventListener('click', loadExamples);

      document.querySelectorAll('.radio-group label').forEach(label => {
        label.addEventListener('click', () => setKind(label.dataset.kind));
      });

      loadExamples();
    </script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)


@app.get("/api/examples")
def api_examples(x_auth_token: Optional[str] = Header(default=None, alias="X-Auth-Token")):
    _auth_or_401(x_auth_token)
    try:
        examples = _collect_examples()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse({"examples": examples})


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

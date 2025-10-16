import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from experiments import parse_series_smart, parse_inequality, classify_problem_kind
from llm_client import generate_text
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
                f"Sum_{obj.summation_index}={bounds[0]}..{bounds[1]} of {obj.formula} << {obj.conjectured_upper_asymptotic_bound} for {obj.conditions}"
                if len(bounds) == 2
                else f"Series in {obj.summation_index}: {obj.formula}"
            )
            manual_text = (
                f"Consider the series: {obj.formula}, where {obj.summation_index} is summed from {bounds[0]} to {bounds[1]}. "
                f"The domain is {obj.conditions or 'True'}. "
                f"It should be bounded above by {obj.conjectured_upper_asymptotic_bound}."
            )
            entries.append(
                {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "type": "series",
                    "cmd": "series",
                    "summary": summary,
                    "manual_text": manual_text,
                    "details": {
                        "conditions": obj.conditions,
                        "other_variables": obj.other_variables,
                        "conjectured_upper_asymptotic_bound": obj.conjectured_upper_asymptotic_bound,
                    },
                }
            )
        elif isinstance(obj, inequality):
            summary = f"Prove {obj.lhs} << {obj.rhs}"
            if getattr(obj, "domain_description", ""):
                summary += f" for {obj.domain_description}"
            domain_desc = getattr(obj, "domain_description", "").strip()
            if domain_desc.startswith("{") and domain_desc.endswith("}"):
                domain_pretty = domain_desc[1:-1]
            else:
                domain_pretty = domain_desc
            domain_sentence = (
                f"Domain is {domain_pretty}." if domain_pretty and domain_pretty.lower() != "true" else "Domain is True."
            )
            manual_text = (
                f"Prove {obj.lhs} << {obj.rhs}. "
                f"{domain_sentence}"
            )
            entries.append(
                {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "type": "inequality",
                    "cmd": "prove",
                    "summary": summary,
                    "manual_text": manual_text,
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
        justify-content: flex-start;
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
      .card h1 {
        margin: 0;
        font-size: 2rem;
      }
      .card h2 {
        margin: 0 0 0.75rem 0;
        font-size: 1.35rem;
      }
      .card-heading {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 1.25rem;
        flex-wrap: wrap;
      }
      .card-heading .credits {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 0.25rem;
        font-size: 0.9rem;
        color: rgba(98, 107, 123, 0.7);
        font-family: var(--sans);
        font-weight: 400;
        margin-top: 0.25rem;
      }
      .card-heading .credits-label {
        font-size: 0.8rem;
        letter-spacing: 0.01em;
        text-transform: none;
      }
      .card-heading .credits-names {
        display: flex;
        flex-direction: column;
        text-align: right;
        line-height: 1.35;
        gap: 0.2rem;
      }
      .card-heading .credits-paper {
        margin-top: 1.0125rem;
      }
      .card-heading .credits a {
        color: inherit;
        text-decoration: none;
        font-weight: 400;
        transition: color 0.15s ease, text-decoration 0.15s ease;
      }
      .card-heading .credits a:hover {
        color: var(--accent);
        text-decoration: underline;
      }
      .subtitle {
        margin: 0;
        color: var(--muted);
      }
      .footnote {
        max-width: 1200px;
        margin: 0 auto;
        padding: 0 1.5rem 1.5rem;
        font-size: 0.85rem;
        color: rgba(98, 107, 123, 0.75);
        text-align: right;
      }
      .footnote a {
        color: inherit;
        text-decoration: underline;
        text-decoration-color: rgba(98, 107, 123, 0.4);
        transition: color 0.15s ease, text-decoration-color 0.15s ease;
      }
      .footnote a:hover {
        color: var(--accent);
        text-decoration-color: var(--accent);
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
        </div>
        <div id="examples" class="example-list">
          <div class="empty-state" id="examples-empty">Examples load on demand. Click reload if needed.</div>
        </div>
      </aside>
      <main class="main">
        <section class="card">
          <div class="card-heading">
            <h1>Decomp Workspace</h1>
            <div class="credits">
              <span class="credits-label">Created by</span>
              <span class="credits-names">
                <a href="https://vganesh1.github.io/" target="_blank" rel="noopener noreferrer">Vijay Ganesh</a>
                <a href="https://ayushkhaitanrutgers.github.io/" target="_blank" rel="noopener noreferrer">Ayush Khaitan</a>
                <span class="credits-paper"><a href="https://arxiv.org/abs/2510.12350" target="_blank" rel="noopener noreferrer">Paper</a></span>
              </span>
            </div>
          </div>
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
              <label data-kind="auto" class="active"><input type="radio" name="kind" value="auto" checked />Auto</label>
              <label data-kind="series"><input type="radio" name="kind" value="series" />Series</label>
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
            <h2>Summary</h2>
            <span class="status-label" id="summary-status">Idle</span>
          </div>
          <pre id="summary" class="muted">(none)</pre>
        </section>
        <section class="card">
          <div class="results-header">
            <h2>Run Output</h2>
            <span class="status-label" id="output-status">Idle</span>
          </div>
          <pre id="output" class="muted">(none)</pre>
        </section>
      </main>
    </div>
    <p class="footnote">
      Terence Tao's <a href="https://mathoverflow.net/a/463940/91878" target="_blank" rel="noopener noreferrer">MathOverflow post</a> that inspired our tool. His <a href="https://mathstodon.xyz/@tao/115379172603958618" target="_blank" rel="noopener noreferrer">post</a> about our tool.
    </p>

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
        return checked ? checked.value : 'auto';
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
          card.dataset.manualText = item.manual_text || '';
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
        updateStatus('summary-status', 'Running...', 'running');
        updateStatus('output-status', 'Running...', 'running');
        document.getElementById('parsed').textContent = '(running...)';
        document.getElementById('summary').textContent = '(running...)';
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
            updateStatus('summary-status', 'Error', 'error');
            updateStatus('output-status', 'Error', 'error');
            document.getElementById('parsed').textContent = 'Error: ' + (data.detail || res.statusText);
            document.getElementById('summary').textContent = '(none)';
            document.getElementById('output').textContent = '';
            return;
          }
          updateStatus('parsed-status', label ? `Ran ${label}` : 'Completed');
          updateStatus('summary-status', data.summary ? 'Completed' : 'Unavailable');
          updateStatus('output-status', 'Completed');
          document.getElementById('parsed').textContent = data.parsed_repr || JSON.stringify(data.parsed, null, 2);
          document.getElementById('summary').textContent = data.summary || 'Summary unavailable.';
          document.getElementById('output').textContent = data.output || '(no output)';
        } catch (err) {
          updateStatus('parsed-status', 'Error', 'error');
          updateStatus('summary-status', 'Error', 'error');
          updateStatus('output-status', 'Error', 'error');
          document.getElementById('parsed').textContent = 'Request failed: ' + err;
          document.getElementById('summary').textContent = '(none)';
          document.getElementById('output').textContent = '';
        }
      }

      async function runExample(example, cardEl) {
        state.selectedExample = example.name;
        const manualText = typeof example.manual_text === 'string' && example.manual_text.length
          ? example.manual_text
          : (cardEl && cardEl.dataset ? cardEl.dataset.manualText || '' : '');
        document.getElementById('text').value = manualText;
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

        parsed = None
        parsed_repr = None
        obj = None
        try:
            import importlib
            examples_mod = importlib.import_module('examples')
            obj = getattr(examples_mod, req.name, None)
        except Exception:
            obj = None

        problem_kind = None
        if isinstance(obj, series_to_bound):
            problem_kind = "series"
            bounds = obj.summation_bounds if isinstance(obj.summation_bounds, list) else list(obj.summation_bounds)
            parsed = {
                "formula": obj.formula,
                "conditions": obj.conditions,
                "summation_index": obj.summation_index,
                "other_variables": obj.other_variables,
                "summation_bounds": bounds,
                "conjectured_upper_asymptotic_bound": obj.conjectured_upper_asymptotic_bound,
            }
            parsed_repr = (
                "series_to_bound(\n"
                f"    formula=\"{obj.formula}\",\n"
                f"    conditions=\"{obj.conditions}\",\n"
                f"    summation_index=\"{obj.summation_index}\",\n"
                f"    other_variables=\"{obj.other_variables}\",\n"
                f"    summation_bounds={bounds},\n"
                f"    conjectured_upper_asymptotic_bound=\"{obj.conjectured_upper_asymptotic_bound}\"\n"
                ")"
            )
        elif isinstance(obj, inequality):
            problem_kind = "inequality"
            domain = getattr(obj, 'domain_description', '')
            parsed = {
                "variables": getattr(obj, 'variables', ''),
                "domain_description": domain,
                "lhs": getattr(obj, 'lhs', ''),
                "rhs": getattr(obj, 'rhs', ''),
            }
            parsed_repr = (
                "inequality(\n"
                f"    variables=\"{parsed['variables']}\",\n"
                f"    domain_description=\"{domain}\",\n"
                f"    lhs=\"{parsed['lhs']}\",\n"
                f"    rhs=\"{parsed['rhs']}\"\n"
                ")"
            )

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
        combined_output = (proc.stderr or "") + (proc.stdout or "")
        summary = summarize_run(problem_kind or (req.kind or "unknown"), parsed_repr, combined_output)
        return JSONResponse({
            "parsed": parsed,
            "parsed_repr": parsed_repr,
            "output": combined_output,
            "summary": summary,
        })

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Provide text for parsing or use mode='by_name'.")

    def _normalize_kind(kind: Optional[str]) -> Optional[str]:
        if not kind:
            return None
        value = kind.strip().lower()
        if value in ("series", "inequality"):
            return value
        if value == "auto":
            return None
        return None

    selected_kind = _normalize_kind(req.kind)
    classification_error: Optional[str] = None

    if selected_kind is None:
        try:
            selected_kind = classify_problem_kind(text)
        except Exception as exc:
            classification_error = str(exc)
            selected_kind = None

    def _prefer_series_from_heuristics(s: str) -> bool:
        lowered = s.lower()
        series_hints = ("\\sum", "sum[", "∑", "series", "summed from", "partial sum", "sigma")
        inequality_hints = ("<<", "\\ll", "≪", "<=", ">=", "<", ">", "≤", "≥")
        if any(tok in lowered for tok in series_hints):
            # If both hints appear, lean toward series
            if any(tok in lowered for tok in inequality_hints):
                return True
            return True
        if any(tok in lowered for tok in inequality_hints):
            return False
        return False

    parse_errors: Dict[str, str] = {}
    series_obj: Optional[series_to_bound] = None
    inequality_obj: Optional[Tuple[str, str, str, str]] = None

    def _parse_series() -> bool:
        nonlocal series_obj
        if series_obj is not None:
            return True
        try:
            series_obj = parse_series_smart(text)
            return True
        except Exception as exc:
            parse_errors["series"] = str(exc)
            return False

    def _parse_inequality() -> bool:
        nonlocal inequality_obj
        if inequality_obj is not None:
            return True
        try:
            inequality_obj = parse_inequality(text)
            return True
        except Exception as exc:
            parse_errors["inequality"] = str(exc)
            return False

    order: List[str] = []
    if selected_kind == "series":
        order = ["series", "inequality"]
    elif selected_kind == "inequality":
        order = ["inequality", "series"]
    else:
        order = ["series", "inequality"] if _prefer_series_from_heuristics(text) else ["inequality", "series"]

    for kind in order:
        if kind == "series" and _parse_series():
            parsed_repr = (
                "series_to_bound(\n"
                f"    formula=\"{series_obj.formula}\",\n"
                f"    conditions=\"{series_obj.conditions}\",\n"
                f"    summation_index=\"{series_obj.summation_index}\",\n"
                f"    other_variables=\"{series_obj.other_variables}\",\n"
                f"    summation_bounds={series_obj.summation_bounds},\n"
                f"    conjectured_upper_asymptotic_bound=\"{series_obj.conjectured_upper_asymptotic_bound}\"\n"
                ")"
            )
            try:
                output = run_series(series_obj)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Execution failed: {exc}")
            summary = summarize_run("series", parsed_repr, output)
            return JSONResponse({
                "parsed": {
                    "formula": series_obj.formula,
                    "conditions": series_obj.conditions,
                    "summation_index": series_obj.summation_index,
                    "other_variables": series_obj.other_variables,
                    "summation_bounds": series_obj.summation_bounds,
                    "conjectured_upper_asymptotic_bound": series_obj.conjectured_upper_asymptotic_bound,
                },
                "parsed_repr": parsed_repr,
                "output": output,
                "summary": summary,
            })
        if kind == "inequality" and _parse_inequality():
            vars_s, domain_s, lhs, rhs = inequality_obj
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
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Execution failed: {exc}")
            summary = summarize_run("inequality", parsed_repr, output)
            return JSONResponse({
                "parsed": {
                    "variables": vars_s,
                    "domain_description": domain_s,
                    "lhs": lhs,
                    "rhs": rhs,
                },
                "parsed_repr": parsed_repr,
                "output": output,
                "summary": summary,
            })

    detail_parts: List[str] = []
    if classification_error:
        detail_parts.append(f"Classifier error: {classification_error}")
    if parse_errors:
        for label, err in parse_errors.items():
            detail_parts.append(f"{label.capitalize()} parser error: {err}")
    if not detail_parts:
        detail_parts.append("Unable to parse input as a series or inequality.")
    raise HTTPException(status_code=400, detail="; ".join(detail_parts))


def main():
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("webapp:app", host=host, port=port, reload=False)
SUMMARY_SYSTEM = (
    "You review logs from a mathematical decomposition tool. "
    "Summaries must be concise (<=2 sentences), reflect success or failure, and mention the reason when available. "
    "Respond with plain text; no markdown bullets or headings."
)


def summarize_run(kind: str, parsed_repr: Optional[str], output: str) -> str:
    """Generate a short natural-language summary of the computation outcome."""
    def _fallback_summary() -> Optional[str]:
        text = output.strip()
        if not text:
            return None
        lower = text.lower()
        success_markers = (
            "resolve results: {true}",
            "result: true",
            "result: it is proved",
            "proved everywhere",
            "all estimates verified",
            "verification succeeded",
        )
        failure_markers = (
            "resolve results: {false}",
            "result: false",
            "unable to prove",
            "verification failed",
            "execution failed",
            "error:",
            "not proved",
            "not verified",
            "wolfram returned error",
        )
        if any(marker in lower for marker in success_markers):
            if kind == "series":
                return "Verification succeeded; the series bound holds."
            if kind == "inequality":
                return "Verification succeeded; the inequality holds."
            return "Verification succeeded."
        if any(marker in lower for marker in failure_markers):
            if kind == "series":
                return "O-Forge was unable to verify the proposed series bound."
            if kind == "inequality":
                return "O-Forge was unable to complete the inequality proof."
            return "O-Forge was unable to complete the proof."
        return None

    prompt_lines = [
        f"Problem type: {kind or 'unknown'}",
    ]
    if parsed_repr:
        prompt_lines.append("Object specification:")
        prompt_lines.append(parsed_repr)
    prompt_lines.append("")
    prompt_lines.append("Tool log:")
    prompt_lines.append(output.strip() or "(empty output)")
    prompt_lines.append("")
    prompt_lines.append("Write one short sentence confirming success or explaining failure.")
    prompt = "\n".join(prompt_lines)
    try:
        summary = generate_text(
            prompt=prompt,
            system_instruction=SUMMARY_SYSTEM,
            model="gemini-2.5-flash",
            max_output_tokens=128,
        ).strip()
        if summary:
            return summary
        fallback = _fallback_summary()
        if fallback:
            return fallback
        return "O-Forge was unable to summarize the result."
    except Exception as exc:  # pragma: no cover - defensive
        fallback = _fallback_summary()
        if fallback:
            return fallback
        return f"O-Forge was unable to summarize the result (error: {exc})."

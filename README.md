# Decompositions Project

This repo explores using an LLM to propose natural subdomain decompositions for inequality proofs, and then verifies those subdomains with a CAS to produce a proof certificate of the asymptotic estimate.

## What It Does
- Prompts an LLM to propose a minimal set of “natural” subdomains where a target inequality should be trivial.
- Verifies each proposed subdomain by querying a CAS (Mathematica via `wolframscript`) to certify the estimate (a Big-O–style bound).
- Reports whether the inequality is proved on the full domain based on the verified subdomains.

## Prerequisites
- Python 3.9+
- An LLM API key
  - Put your key in a local `.env` file as either `GOOGLE_API_KEY=<your_key>` or `GEMINI_API_KEY=<your_key>`.
  - The code auto-loads `.env` (via `python-dotenv` if present) or parses it directly.
- Computer Algebra System
  - Mathematica installed with `wolframscript` available on your PATH.
    - If it’s not on PATH, set `WOLFRAMSCRIPT=/path/to/wolframscript` in your environment.
  - Note: You can adapt the CAS layer to SageMath, but the current code path uses Mathematica. (See `mathematica_export.py`.)

## Setup
```bash
pip install -r requirements.txt
echo "GOOGLE_API_KEY=your_api_key_here" > .env  # or GEMINI_API_KEY
```

If `wolframscript` is not auto-detected, set its location:
```bash
export WOLFRAMSCRIPT=/usr/local/bin/wolframscript  # example
```

## Run
```bash
python mathematica_export.py
```

This invokes the flow that queries the LLM for subdomains and verifies them with Mathematica. The script prints a status such as `It is proved` when the CAS verifies the inequality under the proposed decomposition.

## Files of Interest
- `mathematica_export.py`: Main entry to run the workflow against Mathematica.
- `llm_client.py`: Minimal client using `google-genai` to call a Gemini model. Loads API key from environment/.env.
- `temporary.py`: Scratch file used for interactive exploration and debugging.

## Notes
- Keep `.env` private. The repo is configured to ignore `.env`. Rotate any leaked secrets immediately.
- If you prefer SageMath, replace the Mathematica evaluation calls in `mathematica_export.py` with Sage commands. The CAS boundary is localized in helper functions like `wl_eval`.


from __future__ import annotations
import os
from typing import Iterable, Optional, Dict, Any
import re

try:
    from google import genai
except ImportError as e:
    raise RuntimeError("Please install the new SDK: pip install google-genai") from e


__all__ = ["configure", "generate_text", "stream_text"]

_client: Optional["genai.Client"] = None


def configure(api_key: Optional[str] = None, **client_kwargs: Any) -> None:
    global _client
    # 1) Prefer explicitly passed key
    key = api_key

    # 2) Try environment vars
    if not key:
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    # 3) Try loading from a .env file (via python-dotenv if present)
    if not key:
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv()
            key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        except Exception:
            # 4) Fallback: minimal .env parser for KEY=VALUE lines
            env_path = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_path):
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip().strip("\"'")
                                os.environ.setdefault(k, v)
                    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
                except Exception:
                    pass

    if not key:
        raise RuntimeError(
            "Missing API key. Set GOOGLE_API_KEY or GEMINI_API_KEY, create a .env with one of them,"
            " or pass api_key=... to configure()."
        )

    _client = genai.Client(api_key=key, **client_kwargs)


def _client_or_configure() -> "genai.Client":
    global _client
    if _client is None:
        configure()
    return _client  # type: ignore[return-value]


def generate_text(
    prompt: str,
    *,
    model: str = "gemini-2.5-flash",
    system_instruction: Optional[str] = None,
    temperature: float = 0.0,
    max_output_tokens: int = 256,
    timeout: Optional[float] = 60.0,
    extra_generation_config: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Non-streaming text generation via the new SDK.

    - system_instruction: if provided, is prepended to the prompt (simple emulation)
    - extra_generation_config: merged into generation_config (e.g., {"top_p": 0.95})
    """
    c = _client_or_configure()
    contents = prompt if not system_instruction else f"{system_instruction.strip()}\n\n{prompt}"

    gen_cfg: Dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        # Keep internal reasoning budget modest so text actually emits.
        "thinking_config": {"include_thoughts": False, "thinking_budget": 256},
    }
    if extra_generation_config:
        gen_cfg.update(extra_generation_config)

    # The google-genai client expects `config`, not `generation_config`.
    # `request_options` is not supported on this method signature here.
    resp = c.models.generate_content(
        model=model,
        contents=contents,
        config=gen_cfg,
    )
    return getattr(resp, "text", "") or ""


def stream_text(
    prompt: str,
    *,
    model: str = "gemini-2.5-flash",
    system_instruction: Optional[str] = None,
    temperature: float = 0.2,
    max_output_tokens: int = 1024,
    timeout: Optional[float] = 60.0,
    extra_generation_config: Optional[Dict[str, Any]] = None,
) -> Iterable[str]:
    """
    Streaming text generation. Yields text chunks as they arrive.
    """
    c = _client_or_configure()
    contents = prompt if not system_instruction else f"{system_instruction.strip()}\n\n{prompt}"

    gen_cfg: Dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        # Keep internal reasoning budget modest so text actually emits.
        "thinking_config": {"include_thoughts": False, "thinking_budget": 256},
    }
    if extra_generation_config:
        gen_cfg.update(extra_generation_config)

    # Use the streaming variant of the API and pass `config`.
    stream = c.models.generate_content_stream(
        model=model,
        contents=contents,
        config=gen_cfg,
    )
    for chunk in stream:
        text = getattr(chunk, "text", None)
        if text:
            yield text

def _parse_bracketed_list(text: str, *, coerce_numbers: bool = False):
    """Extract items from a bracketed list like "[a, b, c]".

    Returns a list of strings by default, or ints/floats when
    `coerce_numbers=True` and items look numeric.
    """
    m = re.search(r"\[([^\]]*)\]", text, flags=re.S)
    if not m:
        return []
    inner = m.group(1)
    parts = [p.strip() for p in inner.split(',') if p.strip()]
    if not coerce_numbers:
        return parts

    def _coerce(p: str):
        if re.fullmatch(r"[+-]?\d+", p):
            try:
                return int(p)
            except Exception:
                return p
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?", p) or re.fullmatch(r"[+-]?\d+(?:[eE][+-]?\d+)", p):
            try:
                return float(p)
            except Exception:
                return p
        return p

    return [_coerce(p) for p in parts]


def api_call(*, prompt: str, parse: bool = False, coerce_numbers: bool = False):
    arr=[]
    i=0
    while True:
        i+=1
        final_value=''
        stream = stream_text(prompt)
        # Join streamed chunks without inserting extra spaces; normalize whitespace.
        b = ''.join(a for a in stream).strip()
        if b in arr:
            final_value = b
            break
        else:
            arr.append(b)
        if i==15:
            print('No common value found')
            break
            
    # most_common is a method; call it. It returns (item, count) pairs.
    if not parse:
        return final_value
    return _parse_bracketed_list(final_value, coerce_numbers=coerce_numbers)

def api_call_series(*, prompt: str):
    arr = []
    for i in range(15):
        stream = stream_text(prompt)
            # Join streamed chunks without inserting extra spaces; normalize whitespace.
        b = ''.join(a for a in stream).strip()
        if b in arr:
            return b
            break
        else:
            arr.append(b)
        if i==14:
            print('Solution not found')
            break
    
if __name__=="__main__":
#     prompt = """Consider the domain x>0 and y>1. Then it is true that xy<= ylog[y]+exp[x]. However, this may be tricky to prove.

# On the other hand, if the domain is divided into appropriate subdomains, then the inequality becomes absolutely trivial to prove.

# Find these subdomains. In most cases, these should be the "natural" subdomains in the context

# Try to find the minimum number of such subdomains. Don't unnecessarily complicate

# Only give me the subdomains. Don't give me any extra words or symbols. Put some thinking into it. Use only mathematical symbols that can be parsed
# by the software Mathematica. In other words, use only <=, >=, <, >, Log[], Exp[], and nothing else. Your output
# should of be of the form [x>0, y>1, subdomain 1, x>0, y>1, subdomain2, ....]"""
    prompt = """<code_editing_rules>
  <guiding_principles>
    – Be precise, avoid conflicting instructions
    – Use natural subdomains so inequality proof is trivial
    – Minimize the number of subdomains
    – Output only subdomains, no extra words or symbols
    – Use only <=, >=, <, >, Log[], Exp[] in the output
  </guiding_principles>

  <task>
    Given domain: x>0, y>1
    Inequality: x*y <= y*Log[y] + Exp[x]
    Find minimal subdomains that make inequality trivial
  </task>

  <output_format>
    [x>0, y>1, subdomain1, x>0, y>1, subdomain2, ...]. Hence, your output should in the form of an array
  </output_format>
</code_editing_rules>
"""
    if api_call(prompt=prompt):
        print(f"This is the common value: {api_call(prompt=prompt)}")

import subprocess, shlex, os, shutil, json
from typing import Any, List
from dataclasses import dataclass

def _resolve_wolframscript() -> str:
    # Prefer explicit env override
    env_path = os.environ.get("WOLFRAMSCRIPT")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    # Try PATH
    which_path = shutil.which("wolframscript")
    if which_path:
        return which_path

    # Common install locations
    for p in ("/usr/local/bin/wolframscript", "/opt/homebrew/bin/wolframscript"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    raise FileNotFoundError(
        "wolframscript not found. Set $WOLFRAMSCRIPT or ensure it's on PATH."
    )

WOLFRAMSCRIPT = _resolve_wolframscript()

def _clean_env() -> dict:
    # strip DYLD* to avoid collisions; preserve PATH
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD")}
    env["PATH"] = os.environ.get("PATH", "")
    return env

def wl_eval(expr: str, form: str = "InputForm") -> str:
    """Evaluate Wolfram Language `expr` and return string in `form`.

    - `form` examples: "InputForm", "FullForm", "OutputForm".
    - Returns the exact textual rendering from wolframscript.
    """
    env = _clean_env()
    wrapped = f'ToString[({expr}), {form}]'
    cmd = [WOLFRAMSCRIPT, "-code", wrapped]
    return subprocess.check_output(cmd, text=True, env=env).strip()

def wl_eval_json(expr: str):
    """Evaluate `expr` and parse result via Wolfram's JSON export.

    Uses ExportString[..., "JSON"] on the Wolfram side, then json.loads.
    Not all symbolic results are JSON-serializable; in that case this raises.
    """
    env = _clean_env()
    wrapped = f'ExportString[({expr}), "JSON"]'
    cmd = [WOLFRAMSCRIPT, "-code", wrapped]
    data = subprocess.check_output(cmd, text=True, env=env).strip()
    return json.loads(data)

def wl_bool(expr: str) -> bool:
    out = wl_eval(expr, form="InputForm")
    if out == "True": return True
    if out == "False": return False
    raise ValueError(f"Unexpected output: {out!r}")

def proof_attempt(vars:str, conditions : List[str] = [], lhs: str='', rhs: str=''):
    conds = ' && '.join(conditions)
    vars = str(vars)
    result = wl_eval(f"""a = Resolve[
   Exists[{{C}}, 
    C > 0 && ForAll[{vars}, Implies[{conds}, {lhs} <= C*{rhs}]]], 
   Reals];
If[BooleanQ[a], a, "Uncertain"]""")
    if result == "True":
        print('Proof complete')
    elif result == "False":
        print("False")
    else:
        for c in range(1): #the extra {} makes it a string. 
            print("c:", c)
            result = wl_eval(f"""witnessBigO[vars_, conds_, lhs_, rhs_, c_] := 
    Module[{{S}}, S = If[conds === {{}}, True, And @@ conds];
    Resolve[ForAll[vars, Implies[S, lhs <= (10^c)*rhs]], Reals]];
    witnessBigO[{vars}, {conds}, {lhs}, 
    {rhs}, {c}]
    """)
            print(result)
            
        
proof_attempt("{x,y}",["y > 1", "x > 0", "x <= 2 Log[y]"],"x*y","y*Log[y] + Exp[x]")

#The output from Mathematica is in the form of a string
        
        


    

    
    

import examples
from series_summation import ask_llm_series
import series_summation
import mathematica_export as wl
from copy import deepcopy

series = deepcopy(examples.series_6)
print('raw other variables original:', repr(series.other_variables))
print('raw conditions original:', repr(series.conditions))

orig = wl.wl_eval_json

def debug(expr):
    print(expr.splitlines()[-6])
    raise SystemExit

wl.wl_eval_json = debug
try:
    ask_llm_series(series)
except SystemExit:
    pass
finally:
    wl.wl_eval_json = orig

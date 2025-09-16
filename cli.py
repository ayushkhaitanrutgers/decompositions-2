import os
import argparse

from series_summation import series_to_bound, ask_llm_series
from mathematica_export import question, try_and_prove

def _load_examples():
    try:
        import examples
    except Exception as e:
        raise SystemExit(f"Failed to import examples.py: {e}")

    series = {
        name: obj
        for name, obj in vars(examples).items()
        if not name.startswith("_") and isinstance(obj, series_to_bound)
    }
    questions = {
        name: obj
        for name, obj in vars(examples).items()
        if not name.startswith("_") and isinstance(obj, question)
    }
    return series, questions

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="decomp",
        description="Run LLM-guided decomposition with CAS verification",
    )
    parser.add_argument(
        "--wolframscript",
        help="Path to wolframscript (overrides env and auto-detect)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # List
    p_list = sub.add_parser("list", help="List available examples")
    # Series
    p_series = sub.add_parser("series", help="Run a series example")
    p_series.add_argument("name", help="Example name in examples.py (e.g., series_1)")
    # Prove
    p_prove = sub.add_parser("prove", help="Run an inequality proof example")
    p_prove.add_argument("name", help="Question name in examples.py (e.g., question_1)")

    args = parser.parse_args()

    if args.wolframscript:
        os.environ["WOLFRAMSCRIPT"] = args.wolframscript

    series_map, question_map = _load_examples()

    if args.cmd == "list":
        if series_map:
            print("Series examples:")
            for n in sorted(series_map): print(f"  - {n}")
        if question_map:
            print("Question examples:")
            for n in sorted(question_map): print(f"  - {n}")
        if not series_map and not question_map:
            print("No examples found in examples.py")
        return

    if args.cmd == "series":
        obj = series_map.get(args.name)
        if obj is None:
            choices = ", ".join(sorted(series_map)) or "<none>"
            raise SystemExit(f"Unknown series '{args.name}'. Choose one of: {choices}")
        ask_llm_series(obj)
        return

    if args.cmd == "prove":
        obj = question_map.get(args.name)
        if obj is None:
            choices = ", ".join(sorted(question_map)) or "<none>"
            raise SystemExit(f"Unknown question '{args.name}'. Choose one of: {choices}")
        try_and_prove(obj)
        return

if __name__ == "__main__":
    main()

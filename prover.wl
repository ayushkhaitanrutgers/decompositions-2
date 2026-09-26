(* ::Package:: *)

(* prover.wl -- a careful Mathematica back end for asymptotic inequalities.

   Question answered:  is there a constant C > 0 with  lhs <= C * rhs  at every
   point of the domain?  (Written  lhs << rhs,  or  lhs = O(rhs).)

   This file is driven by prover.py.  It reads a JSON job description from the
   file named by the environment variable ASYM_SPEC, appends one JSON line per
   step to a log file, and writes the final answer as JSON.  Because the log
   survives a hard kill of the kernel, prover.py can restart the kernel and the
   run continues where it stopped (finished steps are replayed from the log,
   the step that was killed is treated as a timeout).

   Jobs:
     "prove"     -> lhs << rhs on the domain?           (ProveBigO)
     "coverage"  -> do the subdomains cover the domain?  (CheckCoverage)

   Both can also be called directly from Mathematica:
     << "prover.wl"
     ProveBigO["x*y", "x*Log[x] + Exp[y]", {"x", "y"}, {"x > 1", "y > 0"}]
     CheckCoverage[{"x"}, {"x > 0"}, {"x <= 1", "x >= 1"}]
*)

BeginPackage["AsymProver`"];

ProveBigO::usage = "ProveBigO[lhs, rhs, vars, conds, opts] decides whether lhs <= C rhs for some constant C > 0 on the domain given by conds. All four arguments are strings or lists of strings. Returns an Association with keys status, constant, method, formulation, witness, message, warnings.";
CheckCoverage::usage = "CheckCoverage[vars, conds, subdomains] checks that the union of the subdomains (strings) covers the domain given by conds.";
RunJob::usage = "RunJob[specPath] runs the JSON job in specPath (used by prover.py).";

Begin["`Private`"];

(* ------------------------------------------------------------------ *)
(* Small helpers                                                        *)
(* ------------------------------------------------------------------ *)

$timeoutToken = "TIMEOUT";
$defaultTime = 30;
$defaultConstants = {1, 2, 4, 10, 100, 1000, 10^6};
$methods = {"Resolve", "Reduce", "Simplify", "FullSimplify"};

(* ForAll and Exists hold their arguments, so a variable that contains the
   variable list would be taken literally.  These helpers substitute first. *)
forAll[vars_, cond_, e_] := ForAll[vars, cond, e];
exists[vars_, cond_] := Exists[vars, cond];
exists[vars_, cond_, e_] := Exists[vars, cond, e];

SetAttributes[timed, HoldFirst];
timed[expr_, t_] := TimeConstrained[expr, t, $timeoutToken];

classify[r_] := Which[
  r === True, "true",
  r === False, "false",
  r === $timeoutToken, "timeout",
  True, "undecided"];

toStr[e_] := ToString[e, InputForm];
shortStr[e_] := Module[{s = toStr[e]}, If[StringLength[s] > 4000, StringTake[s, 4000] <> " ...", s]];

(* Early exit from a job with a partial result. *)
bail[a_Association] := Throw[a, "AsymProver"];

(* ------------------------------------------------------------------ *)
(* Log file: one JSON object per line.  $logged maps a step label to    *)
(* its record so that a restarted kernel can replay finished steps.     *)
(* ------------------------------------------------------------------ *)

$logPath = None;
$logged = <||>;

appendLog[a_Association] := Module[{s},
  If[$logPath === None, Return[]];
  s = OpenAppend[$logPath];
  WriteString[s, ExportString[a, "JSON", "Compact" -> True], "\n"];
  Close[s]];

loadLog[path_] := Module[{lines, recs},
  $logged = <||>;
  If[path === None || !FileExistsQ[path], Return[]];
  lines = Select[Import[path, "Lines"], StringTrim[#] =!= "" &];
  recs = Quiet[Check[ImportString[#, "RawJSON"], $Failed]] & /@ lines;
  Do[
    If[AssociationQ[r],
      Which[
        r["event"] === "start",
          If[!KeyExistsQ[$logged, r["label"]],
            $logged[r["label"]] = <|"event" -> "done", "label" -> r["label"],
              "result" -> "killed", "value" -> "", "seconds" -> Null|>],
        r["event"] === "done", $logged[r["label"]] = r]],
    {r, recs}]];

(* attempt[label, body, t]: run body under a time limit, log it, return the
   record <|"result" -> "true"|"false"|"undecided"|"timeout"|"killed", "value" -> string|>.
   If the label is already in the log (after a restart) the stored record is
   returned without recomputing. *)
$labelPrefix = "";   (* set by the "eventually" loop so labels stay unique *)
$lastPath = None;    (* the last path along which lhs/rhs was shown to blow up *)
SetAttributes[attempt, HoldRest];
attempt[label0_String, body_, t_] := Module[{t0, r, rec, label = $labelPrefix <> label0},
  If[KeyExistsQ[$logged, label], Return[$logged[label]]];
  appendLog[<|"event" -> "start", "label" -> label, "time" -> AbsoluteTime[]|>];
  t0 = AbsoluteTime[];
  (* Test hook: ASYM_TEST_HANG=<label> makes that step hang for ever, ignoring
     the time limit, so the watchdog in prover.py can be exercised. *)
  If[Environment["ASYM_TEST_HANG"] === label, Module[{i = 0}, While[True, i++]]];
  r = Quiet[timed[body, t]];
  rec = <|"event" -> "done", "label" -> label, "result" -> classify[r],
    "value" -> shortStr[r], "seconds" -> Round[AbsoluteTime[] - t0, 0.01]|>;
  appendLog[rec];
  $logged[label] = rec;
  rec];

(* Same, but give back the value as an expression ($Failed on timeout/kill). *)
$solverHeads = MaxValue | MinValue | Maximize | Minimize | Reduce | Resolve | FindInstance |
  FunctionDomain | Simplify | FullSimplify | PiecewiseExpand | Limit;
SetAttributes[attemptValue, HoldRest];
attemptValue[label_String, body_, t_] := Module[{rec, held},
  rec = attempt[label, body, t];
  If[MemberQ[{"timeout", "killed"}, rec["result"]], Return[$Failed]];
  (* The value is stored as text.  Parse it without evaluating, and refuse to
     re-run a solver call that came back unevaluated (it would hang again). *)
  held = Quiet[Check[ToExpression[rec["value"], InputForm, Hold], $Failed]];
  If[held === $Failed || MatchQ[held, Hold[$solverHeads[___]]], $Failed, ReleaseHold[held]]];

(* ------------------------------------------------------------------ *)
(* Parsing and sanity checks                                            *)
(* ------------------------------------------------------------------ *)

parseOrBail[s_String, what_String] := Module[{e},
  If[StringTrim[s] === "", bail[<|"status" -> "error", "message" -> "Empty " <> what <> "."|>]];
  If[!SyntaxQ[s],
    bail[<|"status" -> "error", "message" -> "Cannot parse " <> what <> ": " <> s|>]];
  e = Quiet[Check[ToExpression[s, InputForm], $Failed]];
  If[e === $Failed,
    bail[<|"status" -> "error", "message" -> "Cannot parse " <> what <> ": " <> s|>]];
  e];

userSymbols[expr_] := DeleteDuplicates@Cases[expr,
  s_Symbol /; Context[s] =!= "System`" && Context[s] =!= "AsymProver`Private`", {0, Infinity}];

unknownHeads[expr_] := DeleteDuplicates@Cases[expr,
  h_Symbol[___] /; Context[h] =!= "System`" && Context[h] =!= "AsymProver`Private`" :> h, {0, Infinity}];

(* A fresh Global` symbol base<>i that does not occur in avoid. *)
freshVar[base_String, avoid_List] := Module[{i = 1, s},
  s = ToExpression["Global`" <> base <> ToString[i]];
  While[MemberQ[avoid, s] && i < 10000,
    i++;
    s = ToExpression["Global`" <> base <> ToString[i]]];
  s];

checkInputs[lhs_, rhs_, vars_, S_] := Module[{heads, free},
  Do[
    If[!MatchQ[v, _Symbol] || Context[v] === "System`",
      bail[<|"status" -> "error", "message" ->
        "Variable is not a plain symbol: " <> toStr[v] <> ". (Names such as C, D, E, I, N, O, K are reserved by Mathematica.)"|>]],
    {v, vars}];
  If[!DuplicateFreeQ[vars],
    bail[<|"status" -> "error", "message" -> "Repeated variable in " <> toStr[vars]|>]];
  heads = unknownHeads[{lhs, rhs, S}];
  If[heads =!= {},
    bail[<|"status" -> "error", "message" ->
      "Unknown function(s): " <> StringRiffle[toStr /@ heads, ", "] <>
      ". Use Mathematica names such as Log[x], Exp[x], Sqrt[x], Abs[x], Max[x, y]. " <>
      "If you meant a product, write it with an explicit *, e.g. xy*(1+z)."|>]];
  free = Complement[userSymbols[{lhs, rhs, S}], vars];
  If[free =!= {},
    bail[<|"status" -> "ill-posed", "message" ->
      "Symbol(s) " <> StringRiffle[toStr /@ free, ", "] <>
      " appear in the inequality or the conditions but are not listed as variables. " <>
      "List every symbol as a variable and give its conditions." <>
      If[MemberQ[toStr /@ free, "e"], " (If e means Euler's number, write E or Exp[...].)", ""] <>
      If[MemberQ[toStr /@ free, "pi"], " (If pi means 3.14159..., write Pi.)", ""]|>]];
  If[!FreeQ[{lhs, rhs}, Equal | Less | LessEqual | Greater | GreaterEqual | And | Or | Not],
    bail[<|"status" -> "error", "message" -> "lhs and rhs must be expressions, not inequalities."|>]];
  If[!FreeQ[{lhs, rhs, S}, Complex | I],
    bail[<|"status" -> "ill-posed", "message" -> "Complex numbers appear in the input; only real inequalities are supported."|>]];
];

(* ------------------------------------------------------------------ *)
(* Reformulations.  A "form" is an Association:                          *)
(*   name, detail, vars, S, lhs, rhs, ineq (a function of the constant)  *)
(* All forms are equivalent to the original statement on the domain, so  *)
(* True or False from any of them is a verdict about the original.       *)
(* ------------------------------------------------------------------ *)

makeForm[name_, detail_, vars_, S_, lhs_, rhs_] :=
  <|"name" -> name, "detail" -> detail, "vars" -> vars, "S" -> S, "lhs" -> lhs, "rhs" -> rhs,
    "ineq" -> With[{l = lhs, r = rhs}, Function[c, l <= c r]]|>;

(* Is  v > 0  (or v >= 0) forced by the conditions?  Cheap and logged. *)
impliesQ[tag_String, vars_, S_, prop_, t_] :=
  attempt["check|" <> tag, Resolve[forAll[Join[vars, $params], S, prop], Reals], Min[t, 15]]["result"] === "true";

condAtoms[S_] := If[Head[S] === And, List @@ S, {S}];

(* Both are logged steps, so a kernel restart replays them instead of
   re-running a call that hung. *)
simplifyCond[S_, newVars_, t_] := Module[{r},
  r = attemptValue["reduce-cond|" <> shortStr[S] <> "|" <> shortStr[newVars], Reduce[S, newVars, Reals], Min[t, 15]];
  If[r === $Failed, simplifyWith[S, True, t], r]];

simplifyWith[e_, assum_, t_] := Module[{r},
  r = attemptValue["simplify|" <> shortStr[e] <> "|" <> shortStr[assum], Simplify[e, assum], Min[t, 15]];
  If[r === $Failed, e, r]];

(* x -> E^u for every variable x that appears inside a Log and is positive on the domain. *)
logForm[F_, t_] := Module[{args, cand, news, rules, vars2, S2, lhs2, rhs2, assum},
  args = Cases[{F["lhs"], F["rhs"]}, Log[a_] :> a, {0, Infinity}];
  cand = Select[F["vars"], Function[v, AnyTrue[args, !FreeQ[#, v] &] &&
    impliesQ["positive|" <> F["name"] <> "|" <> toStr[v], F["vars"], F["S"], v > 0, t]]];
  If[cand === {}, Return[None]];
  news = {};
  Do[AppendTo[news, freshVar["u", Join[F["vars"], news]]], {Length[cand]}];
  rules = Thread[cand -> E^news];
  vars2 = F["vars"] /. Thread[cand -> news];
  assum = And @@ (Element[#, Reals] & /@ news);
  S2 = simplifyCond[simplifyWith[F["S"] /. rules, assum, t], vars2, t];
  lhs2 = simplifyWith[F["lhs"] /. rules, assum && S2, t];
  rhs2 = simplifyWith[F["rhs"] /. rules, assum && S2, t];
  makeForm[If[F["name"] === "original", "log-subst", F["name"] <> "+log-subst"],
    StringRiffle[toStr /@ rules, ", "], vars2, S2, lhs2, rhs2]];

(* x -> t^q for every variable under a q-th root (q = lcm of the root orders). *)
rootForm[F_, t_] := Module[{pairs, cand, qs, news, rules, vars2, S2, lhs2, rhs2, assum},
  pairs = Cases[{F["lhs"], F["rhs"]}, Power[b_, r_Rational] :> {b, Denominator[r]}, {0, Infinity}];
  cand = Select[F["vars"], Function[v, AnyTrue[pairs, !FreeQ[#[[1]], v] &] &&
    impliesQ["positive|" <> F["name"] <> "|" <> toStr[v], F["vars"], F["S"], v > 0, t]]];
  If[cand === {}, Return[None]];
  qs = Function[v, LCM @@ Cases[pairs, {b_, q_} /; !FreeQ[b, v] :> q]] /@ cand;
  news = {};
  Do[AppendTo[news, freshVar["t", Join[F["vars"], news]]], {Length[cand]}];
  rules = Thread[cand -> news^qs];
  vars2 = F["vars"] /. Thread[cand -> news];
  assum = And @@ Thread[news > 0];
  S2 = simplifyCond[simplifyWith[F["S"] /. rules, assum, t] && assum, vars2, t];
  lhs2 = simplifyWith[F["lhs"] /. rules, assum && S2, t];
  rhs2 = simplifyWith[F["rhs"] /. rules, assum && S2, t];
  makeForm["root-subst", StringRiffle[toStr /@ rules, ", "], vars2, S2, lhs2, rhs2]];

(* Shift: a condition  y >= g  (g not containing y, g not a number) becomes
   y = g + s with s >= 0.  Likewise for >, <=, <.  One shift per variable. *)
shiftRule[atom_, vars_] := Module[{v, g, strict, lower},
  {v, g, strict, lower} = Replace[atom, {
    GreaterEqual[a_Symbol, b_] :> {a, b, False, True},
    Greater[a_Symbol, b_] :> {a, b, True, True},
    LessEqual[b_, a_Symbol] :> {a, b, False, True},
    Less[b_, a_Symbol] :> {a, b, True, True},
    LessEqual[a_Symbol, b_] :> {a, b, False, False},
    Less[a_Symbol, b_] :> {a, b, True, False},
    GreaterEqual[b_, a_Symbol] :> {a, b, False, False},
    Greater[b_, a_Symbol] :> {a, b, True, False},
    _ :> {None, None, None, None}}];
  If[v === None || !MemberQ[vars, v] || !FreeQ[g, v] || NumericQ[g] || userSymbols[g] === {}, Return[None]];
  <|"var" -> v, "g" -> g, "strict" -> strict, "lower" -> lower|>];

shiftForm[F_, t_] := Module[{atoms, plans, done = {}, vars2, S2, lhs2, rhs2, news = {}, details = {}, s, rule, extra},
  atoms = condAtoms[F["S"]];
  plans = DeleteCases[shiftRule[#, F["vars"]] & /@ atoms, None];
  plans = DeleteDuplicatesBy[plans, #["var"] &];
  If[plans === {}, Return[None]];
  vars2 = F["vars"]; S2 = F["S"]; lhs2 = F["lhs"]; rhs2 = F["rhs"];
  Do[
    s = freshVar["s", Join[vars2, news]];
    AppendTo[news, s];
    rule = p["var"] -> If[p["lower"], p["g"] + s, p["g"] - s];
    extra = If[p["strict"], s > 0, s >= 0];
    AppendTo[details, toStr[rule]];
    vars2 = vars2 /. p["var"] -> s;
    S2 = (S2 /. rule) && extra;
    lhs2 = lhs2 /. rule; rhs2 = rhs2 /. rule,
    {p, plans}];
  S2 = simplifyCond[simplifyWith[S2, True, t], vars2, t];
  lhs2 = simplifyWith[lhs2, S2, t];
  rhs2 = simplifyWith[rhs2, S2, t];
  makeForm["shift", StringRiffle[details, ", "], vars2, S2, lhs2, rhs2]];

piecewiseForm[F_] := Module[{ineq2, S2},
  If[FreeQ[{F["lhs"], F["rhs"], F["S"]}, Abs | Max | Min | Piecewise | Sign | UnitStep | Clip | Ramp], Return[None]];
  S2 = Quiet[PiecewiseExpand[F["S"]]];
  Append[makeForm["piecewise", "Max/Min/Abs expanded into cases", F["vars"], S2, F["lhs"], F["rhs"]],
    "ineq" -> With[{l = F["lhs"], r = F["rhs"], s = S2}, Function[c, PiecewiseExpand[l <= c r, s]]]]];

buildForms[lhs_, rhs_, vars_, S_, t_] := Module[{orig, forms, lf, rf, sf, slf},
  orig = makeForm["original", "", vars, S, lhs, rhs];
  forms = {orig};
  If[vars === {}, Return[forms]];
  AppendTo[forms, piecewiseForm[orig]];
  lf = logForm[orig, t]; AppendTo[forms, lf];
  rf = rootForm[orig, t]; AppendTo[forms, rf];
  sf = shiftForm[orig, t]; AppendTo[forms, sf];
  If[sf =!= None, slf = logForm[sf, t]; AppendTo[forms, slf]];
  DeleteCases[forms, None]];

(* ------------------------------------------------------------------ *)
(* Decision methods.  Each returns True / False / something else.       *)
(* ------------------------------------------------------------------ *)

runMethod["Resolve", F_, c_] := Resolve[forAll[F["vars"], F["S"], F["ineq"][c]], Reals];
runMethod["Reduce", F_, c_] := Reduce[forAll[F["vars"], F["S"], F["ineq"][c]], Reals];
realAssum[vars_] := If[vars === {}, True, Element[Alternatives @@ vars, Reals]];
runMethod["Simplify", F_, c_] := Simplify[F["ineq"][c], F["S"] && realAssum[F["vars"]]];
runMethod["FullSimplify", F_, c_] := FullSimplify[F["ineq"][c], F["S"] && realAssum[F["vars"]]];

numericConstant[c_] := If[NumericQ[c], N[c], Null];
constantStr[c_] := toStr[c];

witnessStr[vars_, cond_, t_] := Module[{w},
  w = attemptValue["witness|" <> shortStr[cond], FindInstance[cond, vars, Reals], Min[t, 20]];
  If[ListQ[w] && w =!= {}, toStr[First[w]], Null]];

witnessText[vars_, cond_, t_] := Replace[witnessStr[vars, cond, t], Null -> "a point Mathematica did not name"];

statementStr[lhs_, c_, rhs_, vars_, S_] :=
  toStr[lhs] <> " <= " <> toStr[c] <> " * (" <> toStr[rhs] <> ")  for all " <> toStr[vars] <> " with " <> toStr[S];

(* ------------------------------------------------------------------ *)
(* The main job                                                          *)
(* ------------------------------------------------------------------ *)

ProveBigO[lhsS_String, rhsS_String, varsS_, condsS_, OptionsPattern[]] := Catch[Module[
  {t, consts, absolute, lhs, rhs, vars, params, conds, S, res, ev, intVars, extraWarn, pm},
  t = OptionValue["TimeLimit"]; consts = OptionValue["Constants"]; absolute = TrueQ[OptionValue["Absolute"]];

  lhs = parseOrBail[lhsS, "lhs"];
  rhs = parseOrBail[rhsS, "rhs"];
  vars = parseOrBail[#, "variable name"] & /@ Flatten[{varsS}];
  params = parseOrBail[#, "parameter name"] & /@ DeleteCases[Flatten[{OptionValue["Parameters"]}], ""];
  conds = parseOrBail[#, "condition"] & /@ DeleteCases[Flatten[{condsS}], ""];
  S = And @@ conds;
  checkInputs[lhs, rhs, Join[vars, params], S];
  If[Intersection[vars, params] =!= {},
    bail[<|"status" -> "error", "message" -> "A symbol cannot be both a variable and a parameter: " <>
      toStr[Intersection[vars, params]]|>]];

  ev = parseOrBail[#, "eventually-variable name"] & /@ DeleteCases[Flatten[{OptionValue["Eventually"]}], ""];
  If[!SubsetQ[Join[vars, params], ev],
    bail[<|"status" -> "error", "message" -> "Every \"eventually\" symbol must be a variable or parameter: " <>
      toStr[Complement[ev, Join[vars, params]]]|>]];

  (* Integer variables: prove the statement for real values instead (this is
     stronger, so a proof still counts; a disproof does not). *)
  {S, intVars} = relaxIntegers[S, Join[vars, params]];
  extraWarn = If[intVars === {}, {},
    {"The integer condition on " <> StringRiffle[toStr /@ intVars, ", "] <>
     " was relaxed to real values: a proof for real values covers the integers, but a failure for real values is not a disproof."}];

  If[ev === {},
    res = proveOnce[lhs, rhs, vars, params, S, t, consts, absolute],
    (* "Eventually": the estimate need only hold for ev-variables beyond some
       threshold.  Try thresholds in turn; a disproof counts only if the
       ratio blows up along a path on which every ev-variable -> Infinity. *)
    res = None;
    Do[
      $labelPrefix = "T=" <> toStr[T] <> "|";
      $lastPath = None;
      res = proveOnce[lhs, rhs, vars, params, S && (And @@ Thread[ev >= T]), t, consts, absolute];
      $labelPrefix = "";
      If[res["status"] === "proved",
        res["message"] = res["message"] <> " (Holds once " <> StringRiffle[toStr /@ ev, ", "] <> " >= " <> toStr[T] <> ".)";
        Break[]];
      If[res["status"] === "disproved",
        (* A disproof on x >= T counts only if lhs/rhs blows up along a path
           on which every ev-variable -> Infinity; look for one if needed. *)
        pm = None;
        If[!($lastPath =!= None && AllTrue[ev, evPathToInfinityQ[$lastPath, #] &]),
          $labelPrefix = "T=" <> toStr[T] <> "|ev|";
          pm = partialMaxDisproof[If[absolute, Abs[lhs], lhs], If[absolute, Abs[rhs], rhs], Join[vars, params], ev,
            S && (And @@ Thread[ev >= T]), t];
          If[pm =!= None,
            $lastPath = If[pm["path"] === None, Thread[ev -> ev freshVar["t", Join[vars, params]]], pm["path"]],
            $lastPath = limitDisproof[If[absolute, Abs[lhs], lhs], If[absolute, Abs[rhs], rhs], Join[vars, params],
              S && (And @@ Thread[ev >= T]), t]];
          $labelPrefix = ""];
        If[$lastPath =!= None && AllTrue[ev, evPathToInfinityQ[$lastPath, #] &],
          res["message"] = If[pm =!= None,
            "For fixed " <> StringRiffle[toStr /@ ev, ", "] <> " the largest value of lhs/rhs over the other variables is " <>
              toStr[pm["M"]] <> If[pm["path"] === None, ", which is infinite", ", which tends to infinity along " <> toStr[pm["path"]]] <>
              ": the estimate fails for arbitrarily large " <> StringRiffle[toStr /@ ev, ", "] <> ", so no constant and no threshold work.",
            "Along the path " <> toStr[$lastPath] <> " the ratio lhs/rhs tends to infinity, so the estimate fails for arbitrarily large " <>
              StringRiffle[toStr /@ ev, ", "] <> ": no constant and no threshold work."];
          res["method"] = If[pm =!= None, "MaxValue + Limit", "Limit along a path"];
          Break[],
          res = None; Continue[]]];
      If[res["status"] === "ill-posed" || res["status"] === "error", Break[]];
      res = None,
      {T, $thresholds}];
    If[res === None,
      res = <|"status" -> "unknown", "constant" -> Null, "constant_numeric" -> Null, "best_constant" -> Null,
        "method" -> Null, "formulation" -> Null, "formulation_detail" -> Null, "statement" -> Null, "witness" -> Null,
        "warnings" -> {}, "message" -> "Mathematica could not prove the estimate for " <>
        StringRiffle[toStr /@ ev, ", "] <> " >= T for any threshold T tried (" <> toStr[$thresholds] <>
        "), and could not show that it fails for arbitrarily large values."|>]];
  If[intVars =!= {} && res["status"] === "disproved",
    res["status"] = "unknown";
    res["message"] = "For real values the estimate fails (" <> res["message"] <>
      "), but " <> StringRiffle[toStr /@ intVars, ", "] <> " must be an integer, so this is not a disproof."];
  res["warnings"] = Join[extraWarn, Lookup[res, "warnings", {}]];
  res], "AsymProver"];

$thresholds = {1, 10, 100, 1000, 10^6};

evPathToInfinityQ[path_, v_] := Module[{e = v /. path, tt},
  tt = Variables[path[[All, 2]]];
  tt =!= {} && !FreeQ[e, First[tt]] && Quiet[Limit[e, First[tt] -> Infinity]] === Infinity];

(* Replace Element[n, Integers] (and the positive / non-negative variants) by
   real conditions.  Returns {newS, integerVars}. *)
relaxIntegers[S_, syms_] := Module[{atoms, ints = {}, out = {}},
  atoms = condAtoms[S];
  Do[
    Switch[a,
      Element[_Symbol, Integers], AppendTo[ints, a[[1]]],
      Element[_Symbol, PositiveIntegers], AppendTo[ints, a[[1]]]; AppendTo[out, a[[1]] >= 1],
      Element[_Symbol, NonNegativeIntegers], AppendTo[ints, a[[1]]]; AppendTo[out, a[[1]] >= 0],
      Element[_Symbol, NegativeIntegers], AppendTo[ints, a[[1]]]; AppendTo[out, a[[1]] <= -1],
      _, AppendTo[out, a]],
    {a, atoms}];
  {And @@ out, DeleteDuplicates[ints]}];

(* One full search, without or with parameters. *)
proveOnce[lhs_, rhs_, vars_, params_, S_, t_, consts_, absolute_] := Module[{res, pstr},
  If[params === {}, Return[proveCore[lhs, rhs, vars, S, t, consts, absolute]]];

  (* With parameters: the sanity checks quantify over variables and
     parameters together; then look for a constant that may depend on the
     parameters; as a last resort run the ordinary search (short time limit). *)
  pstr = StringRiffle[toStr /@ params, ", "];
  res = proveParametric[lhs, rhs, vars, params, S, t, absolute];
  If[res =!= None, Return[res]];
  res = proveCore[lhs, rhs, Join[vars, params], S, Min[t, 10], {1, 10, 1000}, absolute];
  If[res["status"] === "proved",
    res["message"] = res["message"] <> " The constant does not depend on " <> pstr <> ".";
    Return[res]];
  If[res["status"] =!= "disproved" && res["status"] =!= "unknown", Return[res]];
  res["witness"] = Null;
  res["message"] = If[res["status"] === "disproved",
    "No constant independent of " <> pstr <> " works (" <> res["message"] <>
      ") and Mathematica could not decide whether a constant depending on " <> pstr <> " works.",
    "Mathematica could neither prove nor disprove the estimate, with or without letting the constant depend on " <> pstr <> "."];
  res["status"] = "unknown";
  res];

(* Are lhs and rhs real numbers at every point of the domain?  Returns a list
   of warnings; bails out with "ill-posed" if they are provably not. *)
checkRealValued[lhs_, rhs_, vars_, S_, t_] := Module[{fd, rec, ok},
  fd = attemptValue["check|functiondomain", FunctionDomain[{lhs, rhs}, vars], Min[t, 20]];
  If[fd === $Failed, Return[{"Could not compute where lhs and rhs are real-valued."}]];
  If[fd === True, Return[{}]];
  (* Simplify first: FunctionDomain often answers with clauses such as
     Element[k, Integers] that Resolve over the reals cannot digest. *)
  ok = attempt["check|realvalued-simplify", Simplify[fd, S && realAssum[vars]], Min[t, 15]];
  If[ok["result"] === "true", Return[{}]];
  rec = attempt["check|realvalued", Resolve[forAll[vars, S, fd], Reals], Min[t, 20]];
  Switch[rec["result"],
    "false", bail[<|"status" -> "ill-posed", "message" ->
      "lhs or rhs is not a real number somewhere on the domain (for example at " <>
      witnessText[vars, S && !fd, t] <> "). Both sides must be real-valued; note that x^(1/3) is complex for x < 0 in Mathematica."|>],
    "true", {},
    _, {"Could not confirm that lhs and rhs are real-valued on the whole domain."}]];

(* The search for one constant C that works for every value of vars. *)
proveCore[lhs_, rhs_, vars_, S_, t_, consts_, absolute_] := Catch[Module[
  {warnings = {}, rec, fd, lhsUse, rhsUse, forms, dead = <||>, proved = None, falseAt = None,
   cmax, v, zero, sym, w, cc, out},

  (* 1. Is the domain non-empty? *)
  rec = attempt["check|nonempty", Resolve[exists[vars, S], Reals], Min[t, 20]];
  Switch[rec["result"],
    "false", bail[<|"status" -> "ill-posed", "message" ->
      "The domain is empty: no real values satisfy " <> toStr[S] <> "."|>],
    "true", Null,
    _, AppendTo[warnings, "Could not confirm that the domain is non-empty."]];

  (* 2. Are both sides real numbers everywhere on the domain? *)
  warnings = Join[warnings, checkRealValued[lhs, rhs, vars, S, t]];

  (* 3. Signs.  f << g needs g >= 0.  If f can be negative, lhs <= C rhs is one-sided. *)
  If[absolute,
    lhsUse = Abs[lhs]; rhsUse = Abs[rhs],
    lhsUse = lhs; rhsUse = rhs;
    rec = attempt["check|rhs_sign", Resolve[forAll[vars, S, rhs >= 0], Reals], Min[t, 20]];
    Switch[rec["result"],
      "false", bail[<|"status" -> "ill-posed", "message" ->
        "rhs is negative somewhere on the domain (for example at " <> witnessText[vars, S && rhs < 0, t] <>
        "). For lhs << rhs the right side must be >= 0. If you mean |lhs| <= C |rhs|, run with absolute=True."|>],
      "true", Null,
      _, AppendTo[warnings, "Could not confirm rhs >= 0 on the domain; the constant search assumes it."]];
    rec = attempt["check|lhs_sign", Resolve[forAll[vars, S, lhs >= 0], Reals], Min[t, 20]];
    Switch[rec["result"],
      "false", AppendTo[warnings,
        "lhs is negative somewhere on the domain (for example at " <> witnessText[vars, S && lhs < 0, t] <>
        "). A proof of lhs <= C rhs says nothing about |lhs| there; run with absolute=True to prove |lhs| <= C rhs."],
      "true", Null,
      _, AppendTo[warnings, "Could not confirm lhs >= 0 on the domain."]]];

  (* 4. Reformulations. *)
  forms = buildForms[lhsUse, rhsUse, vars, S, t];

  (* 5. Ladder: constants x forms x methods.  A "false" from any form is
        sound, so we jump to the next constant.  A timeout or an undecided
        answer retires that (form, method) pair. *)
  cmax = Last[consts];
  Do[
    falseAt = None;
    Do[
      Do[
        If[!KeyExistsQ[dead, {F["name"], M}],
          rec = attempt["prove|C=" <> toStr[c] <> "|" <> F["name"] <> "|" <> M, runMethod[M, F, c], t];
          Switch[rec["result"],
            "true", proved = <|"constant" -> c, "method" -> M, "form" -> F|>; Break[],
            "false", falseAt = c; Break[],
            _, dead[{F["name"], M}] = True]],
        {M, $methods}];
      If[proved =!= None || falseAt =!= None, Break[]],
      {F, forms}];
    If[proved =!= None, Break[]],
    {c, consts}];

  If[proved =!= None, bail[finish["proved", proved, warnings, lhsUse, rhsUse, vars, S, Null]]];

  (* 6. Exact best constant via MaxValue of the ratio (Mathematica's exact optimiser). *)
  Do[
    v = attemptValue["maxratio|" <> F["name"],
      MaxValue[{F["lhs"]/F["rhs"], F["S"] && F["rhs"] > 0}, F["vars"]], t];
    If[v === Infinity,
      bail[finish["disproved", <|"method" -> "MaxValue", "form" -> F|>, warnings, lhsUse, rhsUse, vars, S,
        "The ratio lhs/rhs is unbounded on the domain, so no constant C works."]]];
    If[NumericQ[v] || v === -Infinity,
      zero = attempt["zeroset|" <> F["name"],
        Resolve[forAll[F["vars"], F["S"] && F["rhs"] == 0, F["lhs"] <= 0], Reals], t];
      If[zero["result"] === "true",
        bail[finish["proved", <|"constant" -> If[NumericQ[v] && v > 0, v, 1], "method" -> "MaxValue", "form" -> F,
          "best" -> If[NumericQ[v], toStr[v], Null]|>, warnings, lhsUse, rhsUse, vars, S, Null]]];
      If[zero["result"] === "false",
        bail[finish["disproved", <|"method" -> "MaxValue", "form" -> F|>, warnings, lhsUse, rhsUse, vars, S,
          "rhs vanishes at a point of the domain where lhs > 0 (for example at " <>
          witnessText[vars, S && rhsUse == 0 && lhsUse > 0, t] <> "), so no constant C works."]]]],
    {F, forms}];

  (* 7. Symbolic constant: let Mathematica solve for the set of admissible C. *)
  cc = ToExpression["Global`AsymC"];
  Do[
    v = attemptValue["symbolic|" <> F["name"],
      Reduce[forAll[F["vars"], F["S"], F["lhs"] <= cc F["rhs"]], cc, Reals], t];
    sym = readSymbolic[v, cc];
    If[sym =!= None,
      If[sym["status"] === "proved",
        bail[finish["proved", <|"constant" -> sym["constant"], "method" -> "Reduce (symbolic C)", "form" -> F,
          "best" -> sym["best"]|>, warnings, lhsUse, rhsUse, vars, S, Null]],
        bail[finish["disproved", <|"method" -> "Reduce (symbolic C)", "form" -> F|>, warnings, lhsUse, rhsUse, vars, S,
          "Mathematica found that no constant C > 0 makes lhs <= C rhs hold on the whole domain."]]]],
    {F, forms}];

  (* 8. Disproof attempts. *)
  rec = attempt["disprove|zeroset", Resolve[exists[vars, S && rhsUse == 0 && lhsUse > 0], Reals], t];
  If[rec["result"] === "true",
    bail[finish["disproved", <|"method" -> "Resolve", "form" -> First[forms]|>, warnings, lhsUse, rhsUse, vars, S,
      "rhs vanishes at a point of the domain where lhs > 0 (for example at " <>
      witnessText[vars, S && rhsUse == 0 && lhsUse > 0, t] <> "), so no constant C works."]]];
  rec = attempt["disprove|forallC",
    Resolve[forAll[cc, cc > 0, exists[vars, S && lhsUse > cc rhsUse]], Reals], t];
  If[rec["result"] === "true",
    bail[finish["disproved", <|"method" -> "Resolve", "form" -> First[forms]|>, warnings, lhsUse, rhsUse, vars, S,
      "For every constant C > 0 there is a point of the domain where lhs > C rhs."]]];
  If[rec["result"] === "false",
    bail[finish["proved", <|"constant" -> Null, "method" -> "Resolve (existence only)", "form" -> First[forms]|>,
      warnings, lhsUse, rhsUse, vars, S,
      "Mathematica proved that some constant C works but could not compute one."]]];

  (* 8b. Ratio blowing up along a path.  If a path p(t) stays inside the domain
     for all t >= 1 and lhs/rhs -> Infinity along it, then no constant works. *)
  rec = limitDisproof[lhsUse, rhsUse, vars, S, t];
  If[rec =!= None,
    $lastPath = rec;
    bail[finish["disproved", <|"method" -> "Limit along a path", "form" -> First[forms]|>, warnings, lhsUse, rhsUse, vars, S,
      "Along the path " <> toStr[rec] <> " with " <> toStr[First[Variables[rec[[All, 2]]]]] <>
      " -> Infinity, the ratio lhs/rhs tends to infinity, so no constant C works."]]];

  If[Length[vars] >= 2,
    Do[
      rec = partialMaxDisproof[lhsUse, rhsUse, vars, {v}, S, t];
      If[rec =!= None,
        $lastPath = rec["path"];
        bail[finish["disproved", <|"method" -> "MaxValue + Limit", "form" -> First[forms]|>, warnings, lhsUse, rhsUse, vars, S,
          "For fixed " <> toStr[v] <> " the largest value of lhs/rhs over the other variables is " <> toStr[rec["M"]] <>
          If[rec["path"] === None, ", which is infinite", ", which tends to infinity along " <> toStr[rec["path"]]] <>
          ", so no constant C works."]]],
      {v, vars}]];

  (* 9. Give up, but report a counterexample for the largest constant tried if there is one. *)
  w = witnessStr[vars, S && lhsUse > cmax rhsUse, t];
  out = <|"status" -> "unknown", "constant" -> Null, "constant_numeric" -> Null, "method" -> Null,
    "formulation" -> Null, "formulation_detail" -> Null, "statement" -> Null, "witness" -> w,
    "warnings" -> warnings,
    "message" -> If[w === Null,
      "Mathematica could neither prove nor disprove the estimate within the time limit.",
      "Not proved. The inequality fails even with C = " <> toStr[cmax] <> " at " <> w <>
      ", so it is probably false, but Mathematica could not prove that no constant works."]|>;
  out], "AsymProver"];

Options[ProveBigO] = {"TimeLimit" -> $defaultTime, "Constants" -> $defaultConstants, "Absolute" -> False,
  "Parameters" -> {}, "Eventually" -> {}};

(* ------------------------------------------------------------------ *)
(* Constants that may depend on parameters.                            *)
(*   for all params with Sp:  exists C > 0:  for all vars with S: lhs <= C rhs   *)
(* Sp is the part of the conditions that mentions no variable.          *)
(* ------------------------------------------------------------------ *)

$params = {};

proveParametric[lhs_, rhs_, vars_, params_, S_, t_, absolute_] := Catch[Module[
  {lhsUse, rhsUse, Sp, cc, forms, v, ok, c, M, zero, pstr, out, allVars, warnings = {}, rec, fd, shapes, dead},
  allVars = Join[vars, params];
  pstr = StringRiffle[toStr /@ params, ", "];
  Sp = And @@ Select[condAtoms[S], FreeQ[#, Alternatives @@ vars] &];
  cc = ToExpression["Global`AsymC"];

  (* Sanity checks, over variables and parameters together. *)
  rec = attempt["check|nonempty", Resolve[exists[allVars, S], Reals], Min[t, 20]];
  Switch[rec["result"],
    "false", bail[<|"status" -> "ill-posed", "message" ->
      "The domain is empty: no real values satisfy " <> toStr[S] <> "."|>],
    "true", Null,
    _, AppendTo[warnings, "Could not confirm that the domain is non-empty."]];
  warnings = Join[warnings, checkRealValued[lhs, rhs, allVars, S, t]];
  If[absolute,
    lhsUse = Abs[lhs]; rhsUse = Abs[rhs],
    lhsUse = lhs; rhsUse = rhs;
    rec = attempt["check|rhs_sign", Resolve[forAll[allVars, S, rhs >= 0], Reals], Min[t, 20]];
    Switch[rec["result"],
      "false", bail[<|"status" -> "ill-posed", "message" ->
        "rhs is negative somewhere on the domain (for example at " <> witnessText[allVars, S && rhs < 0, t] <>
        "). For lhs << rhs the right side must be >= 0. If you mean |lhs| <= C |rhs|, run with absolute=True."|>],
      "true", Null,
      _, AppendTo[warnings, "Could not confirm rhs >= 0 on the domain; the constant search assumes it."]];
    rec = attempt["check|lhs_sign", Resolve[forAll[allVars, S, lhs >= 0], Reals], Min[t, 20]];
    If[rec["result"] === "false", AppendTo[warnings,
      "lhs is negative somewhere on the domain (for example at " <> witnessText[allVars, S && lhs < 0, t] <>
      "). A proof of lhs <= C rhs says nothing about |lhs| there; run with absolute=True to prove |lhs| <= C rhs."]]];

  $params = params;
  forms = buildForms[lhsUse, rhsUse, vars, S, t];
  $params = {};
  out[status_, info_, msg_] := Module[{r},
    r = finish[status, info, warnings, lhsUse, rhsUse, vars, S, msg];
    If[status === "proved",
      r["statement"] = "for every " <> pstr <> " with " <> toStr[Sp] <> " there is a constant C" <>
        If[Lookup[info, "constant", Null] === Null, "", " = " <> toStr[info["constant"]]] <>
        " such that " <> toStr[lhsUse] <> " <= C * (" <> toStr[rhsUse] <> ") for all " <> toStr[vars] <> " with " <> toStr[S]];
    r];
  (* a. A ladder of candidate constants built from the parameters, such as
        1/eps, 1/eps^2, Exp[1/eps], eps, ...  Each candidate is checked by
        one Resolve over variables and parameters together, so a "true" is a
        complete proof with an explicit constant.  Tried first: it is cheap. *)
  shapes = paramShapes[params, Sp, t];
  dead = <||>;
  Do[
    Do[
      If[!KeyExistsQ[dead, {F["name"], sh}],
        Do[
          rec = attempt["param-ladder|" <> F["name"] <> "|" <> toStr[k sh],
            Resolve[forAll[Join[F["vars"], params], F["S"], F["ineq"][k sh]], Reals], Min[t, 15]];
          If[rec["result"] === "true",
            Throw[out["proved", <|"constant" -> k sh, "method" -> "Resolve", "form" -> F|>,
              "Proved with C = " <> toStr[k sh] <> If[sh === 1, ", which does not depend on ", ", a constant that depends on "] <> pstr <> "." <>
              If[F["name"] === "original", "", " (after the substitution " <> F["detail"] <> ")"]], "AsymParam"]];
          If[rec["result"] =!= "false", dead[{F["name"], sh}] = True; Break[]],
          {k, {1, 10, 1000}}]],
      {F, forms}],
    {sh, shapes}];
  Do[
    (* b. Solve for the admissible constants symbolically, then check that
          for every parameter value some positive constant is admissible. *)
    v = attemptValue["param-symbolic|" <> F["name"],
      Reduce[forAll[F["vars"], F["S"], F["lhs"] <= cc F["rhs"]], cc, Reals], t];
    If[v =!= $Failed && FreeQ[v, ForAll | Exists | Reduce | Resolve],
      ok = attempt["param-symbolic-check|" <> F["name"],
        Resolve[forAll[params, Sp, exists[{cc}, cc > 0 && v]], Reals], t];
      If[ok["result"] === "true",
        c = paramConstant[v, cc, params, Sp, t];
        Throw[out["proved", <|"constant" -> c, "method" -> "Reduce (symbolic C)", "form" -> F,
          "best" -> If[c === Null, Null, toStr[c]]|>,
          "Proved with a constant that depends on " <> pstr <> If[c === Null, "", ": C = " <> toStr[c]] <> "." <>
          If[F["name"] === "original", "", " (after the substitution " <> F["detail"] <> ")"]], "AsymParam"]];
      If[ok["result"] === "false",
        Throw[out["disproved", <|"method" -> "Reduce (symbolic C)", "form" -> F|>,
          "For some admissible values of " <> pstr <> " no constant C works, even one depending on " <> pstr <> "."], "AsymParam"]]];
    (* c. The nested statement directly. *)
    ok = attempt["param-nested|" <> F["name"],
      Resolve[forAll[params, Sp, exists[{cc}, cc > 0, forAll[F["vars"], F["S"], F["lhs"] <= cc F["rhs"]]]], Reals], t];
    If[ok["result"] === "true",
      Throw[out["proved", <|"constant" -> Null, "method" -> "Resolve (existence only)", "form" -> F|>,
        "Proved: for every " <> pstr <> " some constant C works, but Mathematica did not compute it."], "AsymParam"]];
    If[ok["result"] === "false",
      Throw[out["disproved", <|"method" -> "Resolve", "form" -> F|>,
        "For some admissible values of " <> pstr <> " no constant C works, even one depending on " <> pstr <> "."], "AsymParam"]];
    (* d. The exact maximum of the ratio as a function of the parameters. *)
    M = attemptValue["param-maxratio|" <> F["name"],
      MaxValue[{F["lhs"]/F["rhs"], F["S"] && F["rhs"] > 0}, F["vars"]], t];
    If[M =!= $Failed && FreeQ[M, Infinity | ComplexInfinity | Indeterminate | MaxValue | Piecewise | ConditionalExpression | Max | Min],
      ok = attempt["param-maxratio-real|" <> F["name"],
        Simplify[Element[M, Reals], Sp && realAssum[params]], Min[t, 15]];
      If[ok["result"] === "true",
        zero = attempt["param-zeroset|" <> F["name"],
          Resolve[forAll[allVars, S && rhsUse == 0, lhsUse <= 0], Reals], t];
        If[zero["result"] === "true",
          Throw[out["proved", <|"constant" -> Max[M, 1], "method" -> "MaxValue", "form" -> F, "best" -> toStr[M]|>,
            "Proved with a constant that depends on " <> pstr <> ": the largest value of lhs/rhs is " <> toStr[M] <> "." <>
            If[F["name"] === "original", "", " (after the substitution " <> F["detail"] <> ")"]], "AsymParam"]]]],
    {F, forms}];


  (* e. Disproof: fix the parameters at a few admissible values; if for one
        of them the ratio lhs/rhs is unbounded, no constant can work there. *)
  rec = paramDisproof[lhsUse, rhsUse, vars, params, S, Sp, t];
  If[rec =!= None,
    Throw[out["disproved", <|"method" -> rec["method"], "form" -> First[forms]|>,
      "For " <> rec["values"] <> " no constant C works: " <> rec["why"]], "AsymParam"]];
  None], "AsymParam"];

paramDisproof[lhs_, rhs_, vars_, params_, S_, Sp_, t_] := Catch[Module[{insts, Sfix, lfix, rfix, M, path, vstr},
  insts = attemptValue["param-instances", FindInstance[Sp, params, Reals, 3], Min[t, 20]];
  If[!ListQ[insts], Throw[None, "AsymPD"]];
  Do[
    Sfix = S /. inst; lfix = lhs /. inst; rfix = rhs /. inst;
    vstr = StringRiffle[toStr[#[[1]]] <> " = " <> toStr[#[[2]]] & /@ inst, ", "];
    M = attemptValue["param-disprove-maxratio|" <> vstr, MaxValue[{lfix/rfix, Sfix && rfix > 0}, vars], Min[t, 20]];
    If[M === Infinity,
      Throw[<|"method" -> "MaxValue", "values" -> vstr, "why" -> "the ratio lhs/rhs is unbounded on the domain."|>, "AsymPD"]];
    path = limitDisproof[lfix, rfix, vars, Sfix, t, "|" <> vstr];
    If[path =!= None,
      $lastPath = path;
      Throw[<|"method" -> "Limit along a path", "values" -> vstr,
        "why" -> "along the path " <> toStr[path] <> " the ratio lhs/rhs tends to infinity."|>, "AsymPD"]],
    {inst, insts}];
  None], "AsymPD"];

(* Candidate shapes for a parameter-dependent constant. *)
paramShapes[params_, Sp_, t_] := Module[{pos, shapes = {1}, prodInv},
  pos = Select[params, impliesQ["positive|param|" <> toStr[#], params, Sp, # > 0, t] &];
  Do[
    shapes = Join[shapes, {1/p, 1/p^2, Exp[1/p], p, p^2, Exp[p], 1/p^3, Exp[2/p], Log[1 + 1/p] + 1}],
    {p, pos}];
  If[Length[pos] > 1, prodInv = Times @@ (1/pos); shapes = Join[shapes, {prodInv, prodInv^2, Times @@ pos}]];
  DeleteDuplicates[shapes]];

(* From the admissible set v (a condition on cc and the parameters), read off
   one explicit constant if it has the form cc >= f or cc > f. *)
paramConstant[v_, cc_, params_, Sp_, t_] := Module[{r},
  r = attemptValue["param-constant", Reduce[v && cc > 0, cc, Reals], Min[t, 15]];
  Which[
    r === $Failed, Null,
    MatchQ[r, GreaterEqual[cc, f_]], r[[2]],
    MatchQ[r, Greater[cc, f_]], r[[2]] + 1,
    MatchQ[r, And[___, GreaterEqual[cc, f_], ___]], First[Cases[r, GreaterEqual[cc, f_] :> f]],
    MatchQ[r, And[___, Greater[cc, f_], ___]], First[Cases[r, Greater[cc, f_] :> f]] + 1,
    True, Null]];

(* Paths through a base point of the domain: every variable scaled by t or by
   1/t, or one variable at a time.  Returns the path (a list of rules) along
   which lhs/rhs provably tends to +Infinity, else None. *)
limitDisproof[lhs_, rhs_, vars_, S_, t_, tag_String: ""] := Module[{tt, w, base, paths = {}, ok, lim, i, found = None},
  If[vars === {}, Return[None]];
  tt = freshVar["t", vars];
  w = attemptValue["path|base" <> tag, FindInstance[S && rhs > 0, vars, Reals], Min[t, 20]];
  base = If[ListQ[w] && w =!= {}, vars /. First[w], ConstantArray[1, Length[vars]]];
  If[!VectorQ[base, NumericQ], Return[None]];
  AppendTo[paths, Thread[vars -> base tt]];
  AppendTo[paths, Thread[vars -> base/tt]];
  Do[
    AppendTo[paths, Thread[vars -> ReplacePart[base, i -> base[[i]] tt]]];
    AppendTo[paths, Thread[vars -> ReplacePart[base, i -> base[[i]]/tt]]],
    {i, Length[vars]}];
  paths = DeleteDuplicates[paths];
  Do[
    ok = attempt["path|inside" <> tag <> "|" <> shortStr[p], Resolve[forAll[{tt}, tt >= 1, (S && rhs > 0) /. p], Reals], Min[t, 15]];
    If[ok["result"] === "true",
      lim = attemptValue["path|limit" <> tag <> "|" <> shortStr[p], Limit[(lhs/rhs) /. p, tt -> Infinity], Min[t, 20]];
      If[lim === Infinity, found = p; Break[]]],
    {p, paths}];
  found];

(* Partial maximisation: M(outer) = sup over the other variables of lhs/rhs,
   computed exactly by MaxValue with the outer variables symbolic.  If M is a
   plain expression in the outer variables and M -> Infinity along a path in
   the outer variables (or M is Infinity for every outer value), no constant
   works, whatever the outer values -- so this also disproves "eventually"
   statements.  Returns <|"path" -> ..., "M" -> ...|> or None. *)
partialMaxDisproof[lhs_, rhs_, vars_, outer_, S_, t_, tag_String: ""] := Catch[Module[
  {others, Sout, M, M2, tt, w, base, paths = {}, ok, lim, i},
  others = Complement[vars, outer];
  If[others === {} || outer === {}, Throw[None, "AsymPM"]];
  Sout = And @@ Select[condAtoms[S], FreeQ[#, Alternatives @@ others] &];
  M = attemptValue["pmax" <> tag <> "|" <> shortStr[outer],
    Assuming[Sout, MaxValue[{lhs/rhs, S && rhs > 0}, others]], t];
  If[M === $Failed || !FreeQ[M, MaxValue], Throw[None, "AsymPM"]];
  M2 = attemptValue["pmax-simplify" <> tag <> "|" <> shortStr[outer], Simplify[M, Sout && realAssum[outer]], Min[t, 15]];
  If[M2 === $Failed, M2 = M];
  If[M2 === Infinity, Throw[<|"path" -> None, "M" -> M2|>, "AsymPM"]];
  If[!FreeQ[M2, Piecewise | ConditionalExpression | Indeterminate | ComplexInfinity | DirectedInfinity | Max | Min],
    Throw[None, "AsymPM"]];
  tt = freshVar["t", vars];
  w = attemptValue["pmax-base" <> tag <> "|" <> shortStr[outer], FindInstance[Sout, outer, Reals], Min[t, 20]];
  base = If[ListQ[w] && w =!= {}, outer /. First[w], ConstantArray[1, Length[outer]]];
  If[!VectorQ[base, NumericQ], Throw[None, "AsymPM"]];
  AppendTo[paths, Thread[outer -> base tt]];
  If[Length[outer] > 1,
    Do[AppendTo[paths, Thread[outer -> ReplacePart[base, i -> base[[i]] tt]]], {i, Length[outer]}]];
  Do[
    ok = attempt["pmax-inside" <> tag <> "|" <> shortStr[p], Resolve[forAll[{tt}, tt >= 1, Sout /. p], Reals], Min[t, 15]];
    If[ok["result"] === "true",
      lim = attemptValue["pmax-limit" <> tag <> "|" <> shortStr[p], Limit[M2 /. p, tt -> Infinity], Min[t, 20]];
      If[lim === Infinity, Throw[<|"path" -> p, "M" -> M2|>, "AsymPM"]]],
    {p, paths}];
  None], "AsymPM"];

(* Interpret the answer of Reduce[forAll[...], C, Reals]. *)
readSymbolic[v_, cc_] := Module[{r},
  Which[
    v === $Failed, None,
    v === True, <|"status" -> "proved", "constant" -> 1, "best" -> Null|>,
    v === False, <|"status" -> "disproved"|>,
    MatchQ[v, GreaterEqual[cc, _?NumericQ]], <|"status" -> "proved", "constant" -> If[v[[2]] > 0, v[[2]], 1], "best" -> toStr[v[[2]]]|>,
    MatchQ[v, Greater[cc, _?NumericQ]], <|"status" -> "proved", "constant" -> If[v[[2]] >= 0, v[[2]] + 1, 1], "best" -> toStr[v[[2]]] <> " (not attained)"|>,
    True,
      r = Quiet[TimeConstrained[Reduce[v && cc > 0, cc, Reals], 10, $Failed]];
      Which[
        r === False, <|"status" -> "disproved"|>,
        MatchQ[r, Greater[cc, _?NumericQ]] && r[[2]] == 0, <|"status" -> "proved", "constant" -> 1, "best" -> Null|>,
        MatchQ[r, GreaterEqual[cc, _?NumericQ]], <|"status" -> "proved", "constant" -> r[[2]], "best" -> toStr[r[[2]]]|>,
        MatchQ[r, Greater[cc, _?NumericQ]], <|"status" -> "proved", "constant" -> r[[2]] + 1, "best" -> toStr[r[[2]]] <> " (not attained)"|>,
        True, None]]];

finish[status_, info_, warnings_, lhs_, rhs_, vars_, S_, msg_] := Module[{F = info["form"], c},
  c = Lookup[info, "constant", Null];
  <|"status" -> status,
    "constant" -> If[c === Null, Null, constantStr[c]],
    "constant_numeric" -> If[c === Null, Null, numericConstant[c]],
    "best_constant" -> Lookup[info, "best", Null],
    "method" -> info["method"],
    "formulation" -> F["name"],
    "formulation_detail" -> F["detail"],
    "statement" -> If[status === "proved" && c =!= Null, statementStr[lhs, c, rhs, vars, S], Null],
    "witness" -> Null,
    "warnings" -> warnings,
    "message" -> If[msg === Null,
      If[status === "proved",
        "Proved: " <> toStr[lhs] <> " <= " <> If[c === Null, "C", toStr[c]] <> " * (" <> toStr[rhs] <> ") on the whole domain" <>
          If[F["name"] === "original", "", " (after the substitution " <> F["detail"] <> ")"] <> ".",
        "Disproved."],
      msg]|>];

(* ------------------------------------------------------------------ *)
(* Coverage of a decomposition                                          *)
(* ------------------------------------------------------------------ *)

CheckCoverage[varsS_, condsS_, subsS_, OptionsPattern[]] := Catch[Module[
  {t, vars, conds, S, subs, rec, union, w = Null, empties = {}, warnings = {}, covered},
  t = OptionValue["TimeLimit"];
  vars = parseOrBail[#, "variable name"] & /@ Flatten[{varsS}];
  conds = parseOrBail[#, "condition"] & /@ DeleteCases[Flatten[{condsS}], ""];
  subs = parseOrBail[#, "subdomain"] & /@ Flatten[{subsS}];
  S = And @@ conds;
  If[subs === {}, bail[<|"status" -> "error", "message" -> "No subdomains given."|>]];
  checkInputs[0, 0, vars, S && (And @@ subs)];
  union = Or @@ subs;
  rec = attempt["coverage|union", Resolve[forAll[vars, S, union], Reals], t];
  covered = Switch[rec["result"], "true", "yes", "false", "no", _, "unknown"];
  If[covered === "no", w = witnessStr[vars, S && !union, t]];
  If[covered === "unknown", AppendTo[warnings, "Could not decide whether the subdomains cover the domain (time limit or too hard)."]];
  Do[
    rec = attempt["coverage|nonempty|" <> toStr[i], Resolve[exists[vars, S && subs[[i]]], Reals], Min[t, 20]];
    If[rec["result"] === "false", AppendTo[empties, i]],
    {i, Length[subs]}];
  <|"status" -> "ok", "covered" -> covered, "uncovered_point" -> w, "empty_subdomains" -> empties,
    "warnings" -> warnings,
    "message" -> Switch[covered,
      "yes", "The subdomains cover the whole domain.",
      "no", "The subdomains do NOT cover the domain; the point " <> toStr[w] <> " is left out.",
      _, "Coverage undecided."]|>], "AsymProver"];

Options[CheckCoverage] = {"TimeLimit" -> $defaultTime};

(* ------------------------------------------------------------------ *)
(* Job runner used by prover.py                                         *)
(* ------------------------------------------------------------------ *)

RunJob[specPath_String] := Module[{spec, res, t, consts},
  spec = Import[specPath, "RawJSON"];
  $logPath = Lookup[spec, "log", None];
  loadLog[$logPath];
  t = Lookup[spec, "time_limit", $defaultTime];
  consts = Lookup[spec, "constants", $defaultConstants];
  res = Catch[
    Which[
      spec["job"] === "prove",
        ProveBigO[spec["lhs"], spec["rhs"], spec["vars"], spec["conds"],
          "TimeLimit" -> t, "Constants" -> consts, "Absolute" -> TrueQ[spec["absolute"]],
          "Parameters" -> Lookup[spec, "params", {}], "Eventually" -> Lookup[spec, "eventually", {}]],
      spec["job"] === "coverage",
        CheckCoverage[spec["vars"], spec["conds"], spec["subdomains"], "TimeLimit" -> t],
      True, <|"status" -> "error", "message" -> "Unknown job type."|>],
    "AsymProver"];
  If[!KeyExistsQ[res, "warnings"], res["warnings"] = {}];
  Export[spec["result"], res, "JSON"];
  Print["@@RESULT@@ ", spec["result"]];
  res];

End[];
EndPackage[];

If[StringQ[Environment["ASYM_SPEC"]], AsymProver`RunJob[Environment["ASYM_SPEC"]]];

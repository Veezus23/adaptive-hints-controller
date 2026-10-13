"""Narrow mathematical parsing and validation, never learner simulation.

Private mathematical keys are computed here and are absent from public tasks.
Typed dictionaries are allowed for isolated software checks or explicitly
provenanced operator parsing; ordinary manual answers use the text parser.
"""
from __future__ import annotations
import ast
import re

VERSION = "regrouping-validator-1"
SMALL = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
WORDS = {**SMALL, **TENS}
COUNT = r"(?:\d+|(?:" + "|".join(WORDS) + r")(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)"
UNIT_COUNTS = re.compile(r"(?P<count>" + COUNT + r")\s*(?P<unit>hundreds?|tens?|ones?|units?)\b", re.I)


def read_number(value):
    if type(value) is int:
        return value
    if not isinstance(value, str):
        raise ValueError("Expected an integer or supported number words")
    value = value.strip().lower().replace("-", " ")
    if value.isdigit():
        return int(value)
    tokens = value.split()
    if not tokens or any(w not in WORDS and w not in {"hundred", "and"} for w in tokens):
        raise ValueError("Unsupported number wording")
    total = 0
    if len(tokens) >= 2 and tokens[1] == "hundred" and tokens[0] in SMALL and 1 <= SMALL[tokens[0]] <= 9:
        total = 100 * SMALL[tokens[0]]
        tokens = tokens[2:]
        if tokens[:1] == ["and"]:
            tokens = tokens[1:]
            if not tokens: raise ValueError("Incomplete number wording")
        if not tokens: return total
    if len(tokens) == 1 and tokens[0] in WORDS:
        return total + WORDS[tokens[0]]
    if len(tokens) == 2 and tokens[0] in TENS and tokens[1] in SMALL and 1 <= SMALL[tokens[1]] <= 9:
        return total + TENS[tokens[0]] + SMALL[tokens[1]]
    raise ValueError("Unsupported number wording")


def exchange(number):
    if type(number) is not int or not 100 <= number <= 999:
        raise ValueError("Exactly one hundred-to-tens exchange in a three-digit whole number is supported")
    h, remainder = divmod(number, 100)
    t, u = divmod(remainder, 10)
    return {"hundreds": h - 1, "tens": t + 10, "ones": u}


def total_value(counts):
    return 100 * counts["hundreds"] + 10 * counts["tens"] + counts["ones"]


def is_departure(text):
    normalized = " ".join(text.lower().split()).rstrip(".!?")
    return normalized in {"stop", "bye", "goodbye", "i have to go", "i have to leave"} or bool(re.search(r"\b(?:gtg|i (?:have|need) to (?:go|leave)|gotta go)\b", normalized))


def _numeric_expression(text):
    node = ast.parse(text.replace("×", "*").replace("−", "-"), mode="eval").body
    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) is int and abs(n.value) <= 100000:
            return n.value
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult)):
            a, b = visit(n.left), visit(n.right)
            return a + b if isinstance(n.op, ast.Add) else a - b if isinstance(n.op, ast.Sub) else a * b
        raise ValueError("Unsupported arithmetic expression")
    return visit(node)


def _reason_fields(raw, number):
    fields = {"raw": raw} if raw.strip() else {}
    if not raw.strip():
        return fields
    equations = []
    residual = raw.lower()
    for match in re.finditer(r"(?<![\w])([\d\s+*×()−-]+)=([\d\s+*×()−-]+)(?![\w])", raw):
        try:
            left, right = _numeric_expression(match[1].strip()), _numeric_expression(match[2].strip())
        except (ValueError, SyntaxError, RecursionError):
            continue
        equations.append({"left": left, "right": right, "valid": left == right,
                          "left_expression": match[1].strip(), "right_expression": match[2].strip()})
        residual = residual.replace(match[0].lower(), " ")
    if equations:
        fields["equations"] = equations
    low = raw.lower()
    equivalence = re.search(r"(?:one|1)\s+hundred\s+(?:equals|is|=|has the same value as|for)\s+(" + COUNT + r")\s+tens", low)
    if equivalence:
        fields["equivalence_tens"] = read_number(equivalence[1])
        residual = residual.replace(equivalence[0], " ")
    if re.search(r"\b(?:same|unchanged|preserv\w*)\s+(?:total\s+)?(?:value|amount)\b|\bvalue\s+(?:stays|is)\s+(?:the\s+)?same\b", low):
        fields["claims_conservation"] = True
    if re.search(r"\b(?:exchang\w*|trad\w*|remov\w*|decreas\w*|lost|lose)\s+(?:one|1)\s+hundred", low):
        fields["hundreds_removed"] = 1
    if re.search(r"\b(?:add\w*|gain\w*|receiv\w*|for)\s+(?:ten|10)\s+tens", low):
        fields["tens_added"] = 10
    allowed_clauses = [r"\b(?:same|unchanged|preserv\w*)\s+(?:total\s+)?(?:value|amount)\b",
                       r"\bvalue\s+(?:stays|is)\s+(?:the\s+)?same\b",
                       r"\b(?:exchang\w*|trad\w*|remov\w*|decreas\w*|lost|lose)\s+(?:one|1)\s+hundred",
                       r"\b(?:add\w*|gain\w*|receiv\w*|for)\s+(?:ten|10)\s+tens"]
    for clause in allowed_clauses:
        residual = re.sub(clause, " ", residual)
    residual = re.sub(r"\b(?:and|the|because|so|i)\b|[\s,.;:]", "", residual)
    if residual:
        fields["unparsed_reasoning"] = True
    return fields


def parse_response(text, question_kind, number):
    """Only explicitly supported count/table/equation forms; preserve raw prose."""
    if not isinstance(text, str):
        raise TypeError("Verbatim response text required")
    stripped = text.strip()
    if is_departure(text):
        return {"departure": True}
    if stripped.lower().rstrip(".!?") in {"yes", "no", "ok", "okay", "thanks", "i understand", "got it"}:
        return {"acknowledgement": True}
    answer, reasoning = stripped, ""
    parts = re.split(r";|\n(?:reasoning|because|explanation)\s*:", stripped, maxsplit=1, flags=re.I)
    if len(parts) > 1:
        answer, reasoning = parts
    # 'because' may introduce a concise supported mathematical explanation.
    if len(parts) == 1 and re.search(r"\bbecause\b", stripped, re.I):
        answer, reasoning = re.split(r"\bbecause\b", stripped, maxsplit=1, flags=re.I)
    counts = {}
    for match in UNIT_COUNTS.finditer(answer):
        unit = match["unit"].lower()
        key = "hundreds" if unit.startswith("hundred") else "tens" if unit.startswith("ten") else "ones"
        if key in counts:
            return {"unparsed": True, "reason": "Repeated unit counts require explicit operator parsing."}
        counts[key] = read_number(match["count"])
    if counts:
        residual = UNIT_COUNTS.sub("", answer).lower()
        residual = re.sub(r"\b(?:and|worth|value|is|are|which|equal|equals)\b|[\s,.:=]", "", residual)
        if residual:
            return {"unparsed": True, "reason": "Unrestricted prose around counts requires operator parsing."}
    if not counts:
        table = re.fullmatch(r"\s*\(?\s*(\d+)\s*[,/]\s*(\d+)\s*[,/]\s*(\d+)\s*\)?\s*", answer)
        if table and question_kind in {"FULL", "P_FULL", "P_REASON", "VERIFY"}:
            counts = dict(zip(("hundreds", "tens", "ones"), map(int, table.groups())))
        else:
            pattern = r"\b(h|t|u|hundreds|tens|ones)\s*[:=]\s*(\d+)\b"
            assignments = re.findall(pattern, answer, re.I)
            if assignments and re.sub(r"[\s,]", "", re.sub(pattern, "", answer, flags=re.I)):
                return {"unparsed": True, "reason": "Unrestricted prose around a table requires operator parsing."}
            for name, value in assignments:
                key = {"h": "hundreds", "t": "tens", "u": "ones"}.get(name.lower(), name.lower())
                if key in counts:
                    return {"unparsed": True, "reason": "Repeated table entries need operator parsing."}
                counts[key] = int(value)
    parsed = {}
    if counts:
        if question_kind == "P_UNIT":
            if "tens" in counts: parsed["unit_count"] = counts["tens"]
            if "ones" in counts: parsed["unit_value"] = counts["ones"]
        elif question_kind in {"P_EQ", "P_COMBINE"} and set(counts) == {"tens"}:
            parsed["tens_count"] = counts["tens"]
        else:
            parsed["counts"] = counts
    else:
        try:
            parsed["bare_number"] = read_number(answer.strip().rstrip("."))
        except ValueError:
            parsed["unparsed"] = True
    parsed["reasoning"] = _reason_fields(reasoning, number)
    return parsed


def _reasoning(parsed, number, kind):
    reason = parsed.get("reasoning", {})
    if not reason:
        return "absent"
    if not isinstance(reason, dict):
        raise ValueError("Reasoning must preserve explicit structured mathematical statements")
    equations = reason.get("equations", [])
    if not isinstance(equations, list) or any(not isinstance(eq, dict) for eq in equations):
        raise ValueError("Equations must be a list of explicit mathematical statements")
    # Structured equations are evaluated by this checker, not trusted booleans.
    for equation in equations:
        if type(equation.get("left")) is not int or type(equation.get("right")) is not int:
            raise ValueError("Equation sides must be integer values")
        for side in ("left", "right"):
            if side + "_expression" in equation:
                if not isinstance(equation[side+"_expression"], str) or _numeric_expression(equation[side+"_expression"]) != equation[side]:
                    raise ValueError("Expression and parsed value disagree")
        if equation.get("left") != equation.get("right"):
            return "inconsistent"
    if "equivalence_tens" in reason and reason["equivalence_tens"] != 10:
        return "inconsistent"
    if "unit_size" in reason and reason["unit_size"] != 10:
        return "inconsistent"
    if "addends" in reason and reason["addends"] != [number // 10 % 10, 10]:
        return "inconsistent"
    if "hundreds_removed" in reason and reason["hundreds_removed"] != 1:
        return "inconsistent"
    if "tens_added" in reason and reason["tens_added"] != 10:
        return "inconsistent"
    if "before_value" in reason and reason["before_value"] != number or "after_value" in reason and reason["after_value"] != number:
        return "inconsistent"
    if reason.get("unparsed_reasoning"):
        return "unresolved"
    def expressions_equivalent_to(expression, value):
        for equation in equations:
            if equation.get("left") != equation.get("right") or equation.get("left") != value:
                continue
            for field in ("left_expression", "right_expression"):
                actual = re.sub(r"\s", "", equation.get(field, "")).replace("×", "*")
                if actual in expression:
                    return True
        return False
    if kind == "P_EQ":
        return "consistent" if reason.get("equivalence_tens") == 10 or expressions_equivalent_to({"10*10"}, 100) else "unresolved"
    if kind == "P_COMBINE":
        target = number // 10 % 10 + 10
        t = number // 10 % 10
        return "consistent" if expressions_equivalent_to({f"{t}+10", f"10+{t}"}, target) or reason.get("addends") == [t, 10] else "unresolved"
    if kind == "P_UNIT":
        target = 10 * (number // 10 % 10)
        t = number // 10 % 10
        return "consistent" if reason.get("unit_size") == 10 or expressions_equivalent_to({f"{t}*10", f"10*{t}"}, target) else "unresolved"
    counts = parsed.get("counts", {})
    if set(counts) == {"hundreds", "tens", "ones"}:
        h, t, u = counts["hundreds"], counts["tens"], counts["ones"]
        expressions = {f"{h}*100+{t}*10+{u}", f"{100*h}+{10*t}+{u}"}
        total_statement = expressions_equivalent_to(expressions, number)
        decomposition = reason.get("conservation_terms")
        if decomposition is not None:
            if not isinstance(decomposition, list) or not all(type(v) is int for v in decomposition) or decomposition != [100*h, 10*t, u] or sum(decomposition) != number:
                return "inconsistent"
            total_statement = True
    else:
        total_statement = False
    exchange_statement = reason.get("hundreds_removed") == 1 and reason.get("tens_added") == 10 and reason.get("claims_conservation") is True
    return "consistent" if total_statement or exchange_statement else "unresolved"


def validate_fields(parsed, number, kind):
    """Mathematical assessment of a structured parse, independent of any ID."""
    exchange(number)  # Domain check before arithmetic.
    if not isinstance(parsed, dict):
        raise ValueError("Response parse must be a structured dictionary")
    for key in ("bare_number", "unit_count", "unit_value", "tens_count"):
        if key in parsed and type(parsed[key]) is not int:
            raise ValueError("Parsed counts and values must be integers, not asserted verdicts")
    if "counts" in parsed and (not isinstance(parsed["counts"], dict) or not set(parsed["counts"]) <= {"hundreds", "tens", "ones"} or any(type(value) is not int or value < 0 for value in parsed["counts"].values())):
        raise ValueError("Unit counts must use supported unit names and nonnegative integers")
    result = {"correctness": "unparseable", "reasoning": "absent", "parsed_fields": parsed,
              "full_representation": False, "operator_required": False, "departure": bool(parsed.get("departure")),
              "ambiguity_reason": None, "category": "unparseable"}
    if parsed.get("departure") or parsed.get("acknowledgement"):
        result["category"] = "departure" if parsed.get("departure") else "acknowledgement"
        return result
    reason = _reasoning(parsed, number, kind)
    result["reasoning"] = reason
    if parsed.get("unparsed"):
        result["operator_required"] = True
        return result
    t = number // 10 % 10
    if kind == "P_UNIT":
        if "unit_count" in parsed and "unit_value" in parsed:
            right = parsed["unit_count"] == t and parsed["unit_value"] == 10 * t
            result.update(correctness="correct" if right else "incorrect", category="unit_correct" if right else "unit_mismatch")
        elif "unit_count" in parsed or "unit_value" in parsed or "bare_number" in parsed:
            result.update(correctness="partial", category="unit_incomplete")
    elif kind in {"P_EQ", "P_COMBINE"}:
        count = parsed.get("tens_count", parsed.get("bare_number"))
        expected = 10 if kind == "P_EQ" else t + 10
        if count == expected * 10 and "bare_number" in parsed:
            result.update(correctness="ambiguous", category="value_count_ambiguity", ambiguity_reason="This could be a value in ones instead of a count of tens; request explicit units.")
        elif count is not None:
            right = count == expected
            result.update(correctness="correct" if right else "incorrect", category=("equivalence_" if kind == "P_EQ" else "combine_") + ("correct" if right else "incorrect"))
    else:
        counts = parsed.get("counts", {})
        if set(counts) == {"hundreds", "tens", "ones"} and all(type(v) is int and v >= 0 for v in counts.values()):
            right = counts == exchange(number)
            result.update(correctness="correct" if right else "incorrect", full_representation=True,
                          category="full_correct" if right else "conservation_incorrect" if total_value(counts) != number else "exchange_incorrect")
        elif counts or "bare_number" in parsed:
            result.update(correctness="partial", category="partial_representation")
    if result["correctness"] == "unparseable":
        result["operator_required"] = True
    if result["correctness"] == "correct" and reason == "inconsistent":
        result["category"] = "answer_correct_reasoning_inconsistent"
    elif result["correctness"] == "correct" and reason in {"absent", "unresolved"}:
        result["category"] = "answer_correct_reasoning_unverified"
    return result

#!/usr/bin/env python3
"""값 규칙 — 관찰 답(예/아니오)의 조합으로 칸의 값을 정하는 표. 과제를 모른다.

정의 문서의 칸 절에 `### 관찰`과 `### 값 규칙`이 있으면, 판독자는 값을 고르지 않고 관찰 항목에만 답한다.
값은 이 모듈이 표를 위에서부터 적용해 낸다 — 첫 번째로 참인 줄의 값, 어느 줄도 아니면 «그 밖» 줄의 값.

## 왜 값을 코드가 정하나

판독자에게 값을 물으면, 값을 먼저 정하고 사진에서 근거를 끼워 맞춘다 — 근거 하나가 없어도 «그 값이니 있겠지»가 된다.
사실(«이 표지가 사진에 있나»)만 묻고 그 답이 어느 값이 되는지 모르게 하면 끼워 맞출 자리가 없다. GT를 판독자에게서 가린 것과
같은 원리를 값 자체에도 쓴 것이다. 표를 적용하는 일에는 실수가 없다.

## 문법 — 과제가 코드를 쓰지 않도록 문법만 넓힌다

    식   := 합 ;  합 := 곱 ('|' 곱)* ;  곱 := 부정 ('&' 부정)* ;  부정 := '!' 부정 | 원자
    원자 := 이름 | '(' 식 ')' | 'COUNT(' 접두사 '*' ')' 비교 수
    이름 := 대문자로 시작하는 [A-Z0-9_]*  (그 칸의 `### 관찰`에 있어야 한다)
    비교 := '>=' | '>' | '<=' | '<' | '=='

`COUNT(COMBO_*) >= 2`는 이름이 `COMBO_`로 시작하는 관찰 가운데 참인 것의 수다(«서로 다른 묶음 둘 이상»).
식은 JSON 트리로 바꿔 워크플로우(`gt-review.js`)에 넘긴다 — 워크플로우는 트리를 계산만 한다(파서가 둘이면 어긋난다).

## 가림 검사 — 중복·충돌을 코드가 확정한다

관찰이 n개면 조합은 2^n개다. 조합마다 «어느 줄이 먼저 걸리나»를 세어, 한 번도 먼저 걸리지 않는 줄은 앞줄에 **가려진** 줄이다 —
앞줄과 중복이거나(같은 값), 앞줄과 충돌해 영영 쓰이지 않거나(다른 값). 어느 쪽이든 정책에 둘 이유가 없어 로더가 멈춘다.
관찰이 너무 많으면(`MAX_EXHAUSTIVE` 초과) 다 대입하지 않고 멈춘다 — 한 칸의 관찰은 짧아야 판독자도 정확하다.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

MAX_EXHAUSTIVE = 14
MAX_EXPR = 300  # 식 한 줄의 글자 수 — 사람이 읽을 수 있는 길이. 넘으면 칸을 나누거나 관찰을 더 만든다
_TOKEN = re.compile(r"\s*(COUNT\(\s*([A-Z][A-Z0-9_]*)\*\s*\)|[A-Z][A-Z0-9_]*|>=|<=|==|>|<|\d+|[()&|!])")
_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
COMPARE = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b,
           "<": lambda a, b: a < b, "==": lambda a, b: a == b}


class DeriveError(ValueError):
    pass


def tokenize(expr: str) -> list[str]:
    tokens, pos, text = [], 0, expr.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise DeriveError(f"식을 읽지 못했습니다: «{text[pos:pos + 12]}» — 이름(대문자)·&·|·!·괄호·COUNT(접두사*) >= 수만 씁니다.")
        tokens.append(match.group(1).replace(" ", ""))
        pos = match.end()
    return tokens


def parse(expr: str) -> dict[str, Any]:
    """식 → JSON 트리. {"op":"name","name":…} · {"op":"not","arg":…} · {"op":"and"/"or","args":[…]} ·
    {"op":"count","prefix":…,"cmp":">=","n":2}"""
    if "\n" in expr or "\r" in expr:
        raise DeriveError("식은 한 줄로 적습니다.")
    if len(expr) > MAX_EXPR:
        raise DeriveError(f"식이 너무 깁니다({MAX_EXPR}자 이내) — 칸을 나누거나 관찰 항목을 더 만들어 주세요.")
    tokens = tokenize(expr)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take(expected: str | None = None) -> str:
        nonlocal pos
        token = peek()
        if token is None or (expected is not None and token != expected):
            raise DeriveError(f"식이 끝나지 않았거나 «{expected}»가 필요한 자리입니다: «{expr}»")
        pos += 1
        return token

    def disj() -> dict[str, Any]:
        args = [conj()]
        while peek() == "|":
            take("|"); args.append(conj())
        return args[0] if len(args) == 1 else {"op": "or", "args": args}

    def conj() -> dict[str, Any]:
        args = [neg()]
        while peek() == "&":
            take("&"); args.append(neg())
        return args[0] if len(args) == 1 else {"op": "and", "args": args}

    def neg() -> dict[str, Any]:
        if peek() == "!":
            take("!")
            return {"op": "not", "arg": neg()}
        return atom()

    def atom() -> dict[str, Any]:
        token = peek()
        if token == "(":
            take("(")
            inner = disj()
            take(")")
            return inner
        if token and token.startswith("COUNT("):
            take()
            prefix = re.match(r"COUNT\(([A-Z][A-Z0-9_]*)\*\)", token).group(1)  # type: ignore[union-attr]
            cmp = take()
            if cmp not in COMPARE:
                raise DeriveError(f"COUNT 뒤에는 >=·>·<=·<·== 가운데 하나가 옵니다: «{expr}»")
            number = take()
            if not number.isdigit():
                raise DeriveError(f"COUNT의 비교 대상은 수입니다: «{expr}»")
            return {"op": "count", "prefix": prefix, "cmp": cmp, "n": int(number)}
        if token and _NAME.match(token):
            take()
            return {"op": "name", "name": token}
        raise DeriveError(f"식에 이름이나 괄호가 와야 할 자리입니다: «{expr}»")

    try:
        tree = disj()
    except RecursionError as error:
        raise DeriveError("식의 괄호·부정이 너무 깊습니다 — 풀어서 적어 주세요.") from error
    if pos != len(tokens):
        raise DeriveError(f"식 끝에 남은 글자가 있습니다: «{' '.join(tokens[pos:])}» — «{expr}»")
    return tree


def names(tree: dict[str, Any]) -> set[str]:
    op = tree["op"]
    if op == "name":
        return {tree["name"]}
    if op == "not":
        return names(tree["arg"])
    if op in ("and", "or"):
        return set().union(*(names(arg) for arg in tree["args"]))
    return set()


def prefixes(tree: dict[str, Any]) -> set[str]:
    op = tree["op"]
    if op == "count":
        return {tree["prefix"]}
    if op == "not":
        return prefixes(tree["arg"])
    if op in ("and", "or"):
        return set().union(*(prefixes(arg) for arg in tree["args"]))
    return set()


def evaluate(tree: dict[str, Any], answers: dict[str, bool]) -> bool:
    op = tree["op"]
    if op == "name":
        return bool(answers.get(tree["name"]))
    if op == "not":
        return not evaluate(tree["arg"], answers)
    if op == "and":
        return all(evaluate(arg, answers) for arg in tree["args"])
    if op == "or":
        return any(evaluate(arg, answers) for arg in tree["args"])
    if op == "count":
        count = sum(1 for name, value in answers.items() if name.startswith(tree["prefix"]) and value)
        return COMPARE[tree["cmp"]](count, tree["n"])
    raise DeriveError(f"모르는 식 노드: {op}")


def derive(table: dict[str, Any], answers: dict[str, bool]) -> tuple[str, str | None]:
    """(값, 걸린 줄 ID). 어느 줄도 아니면 («그 밖» 값, None)."""
    for rule in table["rules"]:
        if evaluate(rule["ast"], answers):
            return rule["value"], rule["id"]
    return table["else"], None


def pivotal(table: dict[str, Any], answers: dict[str, bool]) -> list[str]:
    """답 하나를 뒤집으면 값이 바뀌는 관찰 — 그 항목이 흔들리면 값이 흔들린다(둘째 눈이 볼 자리)."""
    base = derive(table, answers)[0]
    return [name for name in table["observe"] if derive(table, {**answers, name: not answers.get(name)})[0] != base]


def shadowed(table: dict[str, Any]) -> list[str]:
    """어떤 관찰 조합에서도 먼저 걸리지 않는 줄 — 앞줄에 가려졌다(중복 또는 충돌)."""
    observe = list(table["observe"])
    if len(observe) > MAX_EXHAUSTIVE:
        raise DeriveError(f"관찰이 {len(observe)}개라 모든 조합을 확인할 수 없습니다(최대 {MAX_EXHAUSTIVE}) — 칸을 나누거나 관찰을 줄여 주세요.")
    hit: set[str] = set()
    for bits in itertools.product((False, True), repeat=len(observe)):
        _, rule_id = derive(table, dict(zip(observe, bits)))
        if rule_id:
            hit.add(rule_id)
    return [rule["id"] for rule in table["rules"] if rule["id"] not in hit]


def else_reachable(table: dict[str, Any]) -> bool:
    observe = list(table["observe"])
    return any(derive(table, dict(zip(observe, bits)))[1] is None
               for bits in itertools.product((False, True), repeat=len(observe)))


def why_shadowed(table: dict[str, Any], rule_id: str, names: dict[str, str] | None = None) -> str:
    """가려진 줄이 왜 쓰이지 않는지 사람이 읽을 한 문장 — 그 줄이 참인 조합 하나와 그때 먼저 걸리는 줄. 참인 조합이 없으면 그렇다고.
    `names`가 있으면 관찰을 이름으로 부른다(화면), 없으면 ID로(정책 문서를 고치는 사람)."""
    names = names or {}
    observe = list(table["observe"])
    rule = next(rule for rule in table["rules"] if rule["id"] == rule_id)
    for bits in itertools.product((False, True), repeat=len(observe)):
        answers = dict(zip(observe, bits))
        if evaluate(rule["ast"], answers):
            _, first = derive(table, answers)
            shown = ", ".join(f"{names.get(name, name)} {'예' if value else '아니오'}" for name, value in answers.items())
            return f"{rule_id}이 참인 사진({shown})에서도 앞줄 {first}이 먼저 걸립니다"
    return f"{rule_id}의 식은 어떤 답으로도 참이 되지 않습니다(스스로 모순)"


def reachable_values(table: dict[str, Any]) -> set[str]:
    """이 표로 나올 수 있는 값 — 허용값 가운데 여기 없는 값은 이 칸에서 AI가 영영 내지 못한다."""
    observe = list(table["observe"])
    return {derive(table, dict(zip(observe, bits)))[0] for bits in itertools.product((False, True), repeat=len(observe))}


def describe(tree: dict[str, Any], names: dict[str, str]) -> str:
    """식 트리 → 사람이 읽는 말. «광» · «넓은 반사» 아님 · (A 또는 B) · «묶음» 가운데 2개 이상. 정책 페이지와 화면이 쓴다."""
    op = tree["op"]
    if op == "name":
        return f"«{names.get(tree['name'], tree['name'])}»"
    if op == "not":
        inner = describe(tree["arg"], names)
        return f"{inner} 아님" if tree["arg"]["op"] == "name" else f"({inner}) 아님"
    if op in ("and", "or"):
        parts = [describe(arg, names) for arg in tree["args"]]
        parts = [f"({part})" if arg["op"] in ("and", "or") and arg["op"] != op else part for part, arg in zip(parts, tree["args"])]
        return (" · " if op == "and" else " 또는 ").join(parts)
    if op == "count":
        members = [label for key, label in names.items() if key.startswith(tree["prefix"])]
        group = "·".join(members) if members else tree["prefix"] + "*"
        words = {">=": "개 이상", ">": "개 넘게", "<=": "개 이하", "<": "개 미만", "==": "개"}[tree["cmp"]]
        return f"«{group}» 가운데 {tree['n']}{words}"
    raise DeriveError(f"모르는 식 노드: {op}")


def table_changes(old: dict[str, Any], new: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    """표를 바꾸면 어떤 관찰 조합의 값이 바뀌나 — 바뀌는 조합 수와 예시. 사람이 «이 줄을 넣으면 무엇이 달라지나»를 넣기 전에 본다."""
    observe = list(dict.fromkeys([*old["observe"], *new["observe"]]))
    if len(observe) > MAX_EXHAUSTIVE:
        raise DeriveError(f"관찰이 {len(observe)}개라 모든 조합을 확인할 수 없습니다(최대 {MAX_EXHAUSTIVE}).")
    count, examples, overrides = 0, [], []
    for bits in itertools.product((False, True), repeat=len(observe)):
        answers = dict(zip(observe, bits))
        (before, first), after = derive(old, answers), derive(new, answers)[0]
        if before != after:
            count += 1
            if first and first not in overrides:
                overrides.append(first)  # 이 변경이 다른 값으로 덮는 기존 줄 — «그 밖»이 아니라 사람이 적은 줄의 판단을 바꾼다
            if len(examples) < limit:
                examples.append({"answers": answers, "before": before, "after": after})
    return {"changed": count, "total": 2 ** len(observe), "examples": examples, "overrides": overrides}


def covered_same(table: dict[str, Any], rule_id: str) -> list[str]:
    """가려진 줄이 참인 모든 조합에서 먼저 걸리는 앞줄이 **같은 값**을 내면 그 앞줄들 — 새 줄은 중복이라 넣을 까닭이 없다.
    다른 값을 내는 앞줄이 하나라도 있으면 빈 목록(중복이 아니라 충돌이다)."""
    observe = list(table["observe"])
    rule = next(rule for rule in table["rules"] if rule["id"] == rule_id)
    covering: list[str] = []
    for bits in itertools.product((False, True), repeat=len(observe)):
        answers = dict(zip(observe, bits))
        if not evaluate(rule["ast"], answers):
            continue
        value, first = derive(table, answers)
        if first == rule_id or value != rule["value"]:
            return []
        if (first or "그 밖") not in covering:
            covering.append(first or "그 밖")
    return covering

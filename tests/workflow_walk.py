"""A three-valued reading of a GitHub Actions `if:` expression, for the workflow tests.

Moved here from the planner slot's wiring tests when DRE-8617 deleted that
file with the slot it tested. The tests that walk `plan.yml`'s
gates import it from here: `test_planner_allowance_wiring.py`,
`test_plan_critic_wiring.py`, `test_plan_critic_scenario.py` and
`test_plan_escalation_reason_file.py`.

`Walk(known, skipped).evaluate(expr)` answers a str / bool / float / None when
the walk KNOWS the value, and an `Unknown` when it does not; `truth(value)` is
its truthiness, None when unknown. A step is unreachable only when its gate is
KNOWN falsy.
"""
from __future__ import annotations

import re


# --------------------------------------------------------------------------- #
# A three-valued reading of an Actions `if:` expression                        #
# --------------------------------------------------------------------------- #
#
# A value is a Python str / bool / float / None when the walk KNOWS it, and an
# `Unknown` when it does not — any output of a step that may have run, any
# `env.` / `github.` / `inputs.` / `vars.` read, `success()` / `failure()`.
# A step is unreachable only when its gate is KNOWN falsy; an unknown gate
# might run. The walk is therefore conservative in the direction that matters:
# it can call a step reachable that is not, never the reverse.


class Unknown:
    """A value the walk cannot know, with the truthiness it CAN know, if any."""

    def __init__(self, truth: bool | None = None):
        self.truth = truth


def truth(value) -> bool | None:
    if isinstance(value, Unknown):
        return value.truth
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, float):
        return value != 0
    return bool(value)


_TOKEN = re.compile(r"""
    \s*(?:
      (?P<str>'(?:[^']|'')*')
     |(?P<num>-?\d+(?:\.\d+)?)
     |(?P<op>==|!=|<=|>=|&&|\|\||[!<>(),\[\]])
     |(?P<name>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_*][A-Za-z0-9_\-]*)*)
    )""", re.X)


def tokenize(expr: str) -> list[tuple[str, str]]:
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    out, at = [], 0
    while at < len(expr):
        if expr[at:].strip() == "":
            break
        m = _TOKEN.match(expr, at)
        if not m or m.end() == at:
            raise ValueError(f"cannot read {expr[at:]!r} in {expr!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        at = m.end()
    return out


class Walk:
    """Evaluates `if:` expressions against what the walk knows so far."""

    def __init__(self, known: dict[str, object], skipped: set[str]):
        self.known = known
        self.skipped = skipped

    def evaluate(self, expr: str):
        self.toks, self.at = tokenize(expr), 0
        value = self._or()
        if self.at != len(self.toks):
            raise ValueError(f"trailing tokens in {expr!r}")
        return value

    def _peek(self):
        return self.toks[self.at] if self.at < len(self.toks) else (None, None)

    def _take(self, text: str | None = None):
        tok = self._peek()
        if text is not None and tok[1] != text:
            raise ValueError(f"expected {text!r}, found {tok[1]!r}")
        self.at += 1
        return tok

    def _or(self):
        left = self._and()
        while self._peek()[1] == "||":
            self._take()
            right = self._and()
            t = truth(left)
            if t is True:
                continue
            if t is False:
                left = right
            else:
                left = Unknown(True if truth(right) is True else None)
        return left

    def _and(self):
        left = self._cmp()
        while self._peek()[1] == "&&":
            self._take()
            right = self._cmp()
            t = truth(left)
            if t is False:
                continue
            if t is True:
                left = right
            else:
                left = Unknown(False if truth(right) is False else None)
        return left

    def _cmp(self):
        left = self._unary()
        while self._peek()[1] in ("==", "!=", "<", ">", "<=", ">="):
            op = self._take()[1]
            right = self._unary()
            if isinstance(left, Unknown) or isinstance(right, Unknown):
                left = Unknown()
                continue
            a, b = self._norm(left), self._norm(right)
            if op in ("==", "!="):
                left = (a == b) if op == "==" else (a != b)
            else:
                left = Unknown()
        return left

    @staticmethod
    def _norm(value):
        if value is None:
            return ""
        return value.lower() if isinstance(value, str) else value

    def _unary(self):
        if self._peek()[1] == "!":
            self._take()
            t = truth(self._unary())
            return Unknown() if t is None else (not t)
        return self._primary()

    def _primary(self):
        kind, text = self._take()
        if text == "(":
            value = self._or()
            self._take(")")
            return value
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text)
        if kind != "name":
            raise ValueError(f"unexpected {text!r}")
        if self._peek()[1] == "(":
            self._take()
            args = []
            while self._peek()[1] != ")":
                args.append(self._or())
                if self._peek()[1] == ",":
                    self._take()
            self._take(")")
            return self._call(text, args)
        value = self._name(text)
        while self._peek()[1] == "[":
            self._take()
            self._or()
            self._take("]")
            value = Unknown()
        return value

    @staticmethod
    def _call(name: str, args: list):
        if name == "always":
            return True
        return Unknown()  # success(), failure(), cancelled(), contains(), ...

    def _name(self, text: str):
        if text in ("true", "false"):
            return text == "true"
        if text == "null":
            return None
        parts = text.split(".")
        if parts[0] == "steps" and len(parts) >= 3:
            ident = parts[1]
            if ident in self.skipped:
                return "skipped" if parts[2] in ("outcome", "conclusion") else ""
            if parts[2] == "outputs" and len(parts) == 4 and text in self.known:
                return self.known[text]
        return Unknown()

"""Safe oracle expression evaluator (SCENARIOS.md §3).

A restricted AST interpreter — NOT ``eval`` — over the predicate registry. Allowed: predicate
calls, boolean/logic/comparison ops, literals, list/tuple literals, and scenario ``vars`` names.
Anything else raises. ``over_repeats(agg, expr)`` is handled specially at endpoint time: its inner
expression is evaluated across every repeat's context and aggregated (mean/min/max/ci_low).
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from bench.oracle import registry
from bench.oracle.context import RunContext

_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
}


class OracleError(RuntimeError):
    """Raised on a malformed or unsafe oracle expression."""


def _agg(name: str, values: list[float]) -> float:
    if not values:
        return 0.0
    if name == "mean":
        return sum(values) / len(values)
    if name == "min":
        return min(values)
    if name == "max":
        return max(values)
    if name == "ci_low":  # lower bound of a 95% normal CI on the mean
        n = len(values)
        mean = sum(values) / n
        if n < 2:
            return mean
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        return mean - 1.96 * math.sqrt(var / n)
    raise OracleError(f"unknown aggregator {name!r}")


def _const(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    raise OracleError(f"expected a constant, got {ast.dump(node)}")


def _eval(node: ast.AST, ctx: RunContext) -> Any:
    """Evaluate a node against a single context (no over_repeats)."""
    if isinstance(node, ast.Expression):
        return _eval(node.body, ctx)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        if node.id in ctx.vars:
            return ctx.vars[node.id]
        raise OracleError(f"unknown name {node.id!r} (not a scenario var)")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx) for e in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, ctx)
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, ctx) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, ctx)
            fn = _CMP.get(type(op))
            if fn is None:
                raise OracleError(f"unsupported comparator {type(op).__name__}")
            if not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        return _eval_call(node, ctx)
    raise OracleError(f"disallowed expression node {type(node).__name__}")


def _eval_call(node: ast.Call, ctx: RunContext) -> Any:
    if not isinstance(node.func, ast.Name):
        raise OracleError("only bare predicate calls are allowed")
    name = node.func.id
    if name == "over_repeats":
        raise OracleError("over_repeats() is only valid inside an endpoint")
    fn = registry.get(name)
    if fn is None:
        raise OracleError(f"unknown predicate {name!r}")
    args = [_eval(a, ctx) for a in node.args]
    return fn(ctx, *args)


# ----------------------------------------------------------------- public API
def evaluate(expr: str, ctx: RunContext) -> Any:
    """Evaluate a success/leaf expression against one context."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise OracleError(f"bad oracle expression {expr!r}: {exc}") from exc
    return _eval(tree, ctx)


def evaluate_must(name: str, ctx: RunContext) -> bool:
    """A `must` item is a bare predicate name; it must return truthy."""
    fn = registry.get(name)
    if fn is None:
        raise OracleError(f"unknown must-predicate {name!r}")
    return bool(fn(ctx))


def _operand(node: ast.AST, contexts: list[RunContext]) -> Any:
    """A comparison operand in an endpoint: over_repeats aggregates; everything else is a scalar
    on the representative (last) context (constants/vars are repeat-invariant; a bare predicate as
    an operand yields its value)."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "over_repeats"
    ):
        if len(node.args) != 2:
            raise OracleError("over_repeats(agg, expr) takes two args")
        agg = _const(node.args[0])
        values = [float(_eval(node.args[1], c)) for c in contexts]
        return _agg(str(agg), values)
    return _eval(node, contexts[-1])


def _endpoint_top(node: ast.AST, contexts: list[RunContext]) -> bool:
    """A top-level endpoint clause returns a bool across all repeat contexts."""
    if isinstance(node, ast.Expression):
        return _endpoint_top(node.body, contexts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _endpoint_top(node.operand, contexts)
    if isinstance(node, ast.BoolOp):
        vals = [_endpoint_top(v, contexts) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _operand(node.left, contexts)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _operand(comparator, contexts)
            fn = _CMP.get(type(op))
            if fn is None:
                raise OracleError(f"unsupported comparator {type(op).__name__}")
            if not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "over_repeats":
            raise OracleError("over_repeats must be used inside a comparison")
        # a bare predicate endpoint must hold in EVERY repeat
        return all(bool(_eval(node, c)) for c in contexts)
    return bool(_eval(node, contexts[-1]))


def evaluate_endpoint(expr: str, contexts: list[RunContext]) -> bool:
    """Evaluate an endpoint expression (with over_repeats) across repeat contexts."""
    if not contexts:
        return False
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise OracleError(f"bad endpoint expression {expr!r}: {exc}") from exc
    return _endpoint_top(tree, contexts)

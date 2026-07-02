"""S5 条件別リファレンスソルバ（ADR-014: 2機構をシグネチャで分離）。

直交する2機構:
- 規範チェック(normative): 計画時に違反経路を事前棄却し適合経路を選ぶ。
- 来歴記録(provenance): custody連鎖（全通行）を記録し監査に備える。

- OR-full        : 規範ON ＋ 来歴ON → 違反0・監査完全（規範コストは許容）。
- OR-no-normative: 規範OFF ＋ 来歴ON → 最短経路で禁止区画を通過（違反）・監査は可能。
- OR-no-prov     : 規範ON ＋ 来歴OFF → 適合だが custody を再構成できず監査不完全。
- B1             : 規範OFF ＋ 来歴OFF → 違反かつ監査不能（最悪）。
"""

from __future__ import annotations

from collections import deque

from orx.exp.suites.s5_hospital.model import S5World, Transport
from orx.oracle.scenarios.s5 import forbidden_zone_classes

CONDITIONS = ["OR-full", "OR-no-normative", "OR-no-prov", "B1"]
_NORM_AWARE = {"OR-full", "OR-no-prov"}
_PROV_ON = {"OR-full", "OR-no-normative"}


def _adjacency(edges: list[list[str]]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    for k in adj:
        adj[k] = sorted(adj[k])  # 決定的
    return adj


def _bfs(adj: dict[str, list[str]], src: str, dst: str, allowed: set[str]) -> list[str] | None:
    """allowed ノードのみを通る最短経路（決定的: 近傍はソート済み）。"""
    if src not in allowed or dst not in allowed:
        return None
    q: deque[list[str]] = deque([[src]])
    seen = {src}
    while q:
        path = q.popleft()
        node = path[-1]
        if node == dst:
            return path
        for nb in adj.get(node, []):
            if nb in allowed and nb not in seen:
                seen.add(nb)
                q.append([*path, nb])
    return None


def plan_route(condition: str, transport: Transport, world: S5World) -> list[str]:
    adj = _adjacency(world.edges)
    all_nodes = set(world.zone_class)
    if condition in _NORM_AWARE:
        forbidden = forbidden_zone_classes(transport.item_class, world.norms)
        allowed = {z for z in all_nodes if world.zone_class.get(z) not in forbidden}
        allowed |= {transport.src, transport.dst}  # 端点は常に許可
        route = _bfs(adj, transport.src, transport.dst, allowed)
        if route is not None:
            return route
        # 適合経路が無い場合のみ全域（設計上は適合経路が存在する）
    return _bfs(adj, transport.src, transport.dst, all_nodes) or [transport.src, transport.dst]


def shortest_route(transport: Transport, world: S5World) -> list[str]:
    """規範を無視した最短経路（規範コストの基準）。"""
    adj = _adjacency(world.edges)
    return _bfs(adj, transport.src, transport.dst, set(world.zone_class)) or [
        transport.src,
        transport.dst,
    ]


def record_custody(condition: str, route: list[str]) -> list[str]:
    """来歴記録。ONなら全通行、OFFなら端点のみ（監査で連鎖を再構成できない）。"""
    if condition in _PROV_ON:
        return list(route)
    return [route[0], route[-1]] if route else []

"""記号条件付け γ：KGSubgraph -> cond[B,Dc]（world_model.md の契約）。

信念KGから対象区画の部分グラフ（オーダー・作業割当・到着予定・ゾーン制約・直近の乖離）を
抽出し、固定長ベクトルに変換して潜在動態モデルの条件トークンとして渡す（poc_plan.md 5.4）。

2実装を切替可能にする（poc_plan.md 5.4「比較対象として...小型テキストエンコーダも実装」）：
- "rgcn"：R-GCN 風の関係認識メッセージパッシングで埋め込む（既定）。
- "text"：部分グラフをテキスト直列化し、軽量な埋め込み（学習可能なトークン平均）に通す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import torch
from rdflib.query import ResultRow
from torch import Tensor, nn

from gtwm.kg.store import KGStore, load_query

# R-GCN のリレーション語彙（gt-core.ttl のオブジェクトプロパティに対応）。
# 固定長にするため、未知の述語は "other" に落とす。
_RELATIONS = [
    "gt:currentZone",
    "gt:holds",
    "gt:adjacentTo",
    "gt:allowedClass",
    "gt:aggregatedInto",
    "rdf:type",
    "other",
]
_REL_TO_IDX = {r: i for i, r in enumerate(_RELATIONS)}


@dataclass
class KGSubgraph:
    """対象区画に関する部分グラフの薄いラッパー（ノード集合とラベル付きエッジ）。"""

    nodes: list[str] = field(default_factory=list)  # 個体/ゾーン等の CURIE
    edges: list[tuple[int, int, str]] = field(default_factory=list)  # (src_idx,dst_idx,predicate)
    recent_discrepancy_count: int = 0


def extract_subgraph(store: KGStore, focus_zone: str, t: datetime, max_hops: int = 1) -> KGSubgraph:
    """`focus_zone`（例: "gt:Zone_Storage_A"）を中心に、snapshot(t) から局所部分グラフを取り出す。

    hop 0：focus_zone に `gt:currentZone` で属する個体。hop 1：それらの個体が主語/目的語に
    現れる他の全トリプル（`max_hops=1` の既定範囲）。
    """
    snapshot = store.snapshot(t)
    zone_query = load_query("zone_facts")
    rows = [row for row in snapshot.query(zone_query) if isinstance(row, ResultRow)]

    nodes: list[str] = [focus_zone]
    node_to_idx = {focus_zone: 0}
    edges: list[tuple[int, int, str]] = []

    def _idx(curie: str) -> int:
        if curie not in node_to_idx:
            node_to_idx[curie] = len(nodes)
            nodes.append(curie)
        return node_to_idx[curie]

    for row in rows:
        subject = str(row.subject)
        zone = str(row.zone)
        if zone.endswith(focus_zone.split(":")[-1]) or zone == focus_zone:
            s_idx = _idx(subject)
            z_idx = _idx(focus_zone)
            edges.append((s_idx, z_idx, "gt:currentZone"))
            if max_hops >= 1:
                for s2, p2, o2 in snapshot.triples((None, None, None)):
                    if str(s2) == subject:
                        edges.append((s_idx, _idx(str(o2)), str(p2)))

    return KGSubgraph(nodes=nodes, edges=edges)


class RGCNConditioner(nn.Module):
    """簡易 R-GCN：リレーション別の線形変換 + 平均プーリングで固定長ベクトルを作る。"""

    def __init__(self, node_dim: int = 32, cond_dim: int = 256, n_layers: int = 2):
        super().__init__()
        self.node_dim = node_dim
        self.cond_dim = cond_dim
        self.node_embed = nn.Embedding(4096, node_dim)  # ノード名のハッシュ埋め込み
        self.rel_transforms = nn.ModuleList(
            [nn.Linear(node_dim, node_dim) for _ in range(len(_RELATIONS))]
        )
        self.n_layers = n_layers
        self.readout = nn.Linear(node_dim, cond_dim)

    def _hash_node(self, name: str) -> int:
        return hash(name) % 4096

    def forward(self, subgraph: KGSubgraph, device: str) -> Tensor:
        """KGSubgraph -> cond[Dc]（バッチなし。呼び出し側でスタックしてバッチ化する）。"""
        if not subgraph.nodes:
            return torch.zeros(self.cond_dim, device=device)
        idx = torch.tensor(
            [self._hash_node(n) for n in subgraph.nodes], device=device, dtype=torch.long
        )
        h = self.node_embed(idx)  # [N,node_dim]
        for _ in range(self.n_layers):
            messages = torch.zeros_like(h)
            counts = torch.zeros(h.shape[0], device=device)
            for src, dst, pred in subgraph.edges:
                rel_idx = _REL_TO_IDX.get(pred, _REL_TO_IDX["other"])
                messages[dst] = messages[dst] + self.rel_transforms[rel_idx](h[src])
                counts[dst] += 1
            counts = counts.clamp_min(1.0).unsqueeze(-1)
            h = torch.relu(h + messages / counts)
        pooled = h.mean(dim=0)
        return self.readout(pooled)


class TextConditioner(nn.Module):
    """部分グラフをテキスト直列化し、軽量埋め込み（学習可能語彙埋め込みの平均）に通す。"""

    def __init__(self, cond_dim: int = 256, vocab_size: int = 4096):
        super().__init__()
        self.cond_dim = cond_dim
        self.vocab_size = vocab_size
        self.token_embed = nn.Embedding(vocab_size, cond_dim)

    def serialize(self, subgraph: KGSubgraph) -> str:
        lines = [f"discrepancies={subgraph.recent_discrepancy_count}"]
        for src, dst, pred in subgraph.edges:
            lines.append(f"{subgraph.nodes[src]} {pred} {subgraph.nodes[dst]}")
        return " ; ".join(lines)

    def forward(self, subgraph: KGSubgraph, device: str) -> Tensor:
        text = self.serialize(subgraph)
        tokens = text.split()
        if not tokens:
            return torch.zeros(self.cond_dim, device=device)
        ids = torch.tensor(
            [hash(tok) % self.vocab_size for tok in tokens], device=device, dtype=torch.long
        )
        return self.token_embed(ids).mean(dim=0)


class Conditioner(nn.Module):
    """契約：Conditioner(subgraph: KGSubgraph) -> cond[B,Dc]。`impl` で実装を切替える。"""

    def __init__(self, impl: str = "rgcn", cond_dim: int = 256):
        super().__init__()
        if impl not in ("rgcn", "text"):
            raise ValueError(f"未知の conditioning 実装: {impl}")
        self.impl = impl
        self.cond_dim = cond_dim
        self.rgcn = RGCNConditioner(cond_dim=cond_dim)
        self.text = TextConditioner(cond_dim=cond_dim)

    def forward(self, subgraphs: list[KGSubgraph], device: str) -> Tensor:
        module = self.rgcn if self.impl == "rgcn" else self.text
        return torch.stack([module(sg, device) for sg in subgraphs], dim=0)


__all__ = [
    "KGSubgraph",
    "extract_subgraph",
    "RGCNConditioner",
    "TextConditioner",
    "Conditioner",
]

"""S7 エピソード生成（シード決定的）: 入居者ごとに ID無し小物1個＋台帳メモ署名。"""

from __future__ import annotations

from orx.common.seeding import SeedTree
from orx.exp.suites.s7_ownership.grounding import (
    note_signature,
    owner_offset,
    prototype,
    true_signature,
)
from orx.exp.suites.s7_ownership.model import S7Episode, S7Ledger, S7Object, S7World


def generate_episode(world: S7World, seed: int, sep: float) -> S7Episode:
    proto = prototype(world.item_type, world.embedding_dim)
    obj_rng = SeedTree(seed).child("s7-obj").rng()
    note_rng = SeedTree(seed).child("s7-note").rng()
    objects: list[S7Object] = []
    note_embedding: dict[str, list[float]] = {}
    last_seen: dict[str, str] = {}
    for r in world.residents:
        off = owner_offset(r, world.embedding_dim)
        zone = world.resident_zone[r]
        objects.append(
            S7Object(
                true_owner=r,
                zone=zone,
                embedding=true_signature(proto, off, sep, world.episode_sigma, obj_rng),
            )
        )
        note_embedding[r] = note_signature(proto, off, sep, world.note_sigma, note_rng)
        last_seen[r] = zone
    ledger = S7Ledger(note_embedding=note_embedding, last_seen_zone=last_seen)
    return S7Episode(objects=objects, ledger=ledger, prototype=proto)

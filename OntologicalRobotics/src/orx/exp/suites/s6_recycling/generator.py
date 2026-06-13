"""S6 エピソード生成（シード決定的）: ID無し物体の流入列＋真クラス＋視覚埋め込み。"""

from __future__ import annotations

from orx.common.seeding import SeedTree
from orx.exp.suites.s6_recycling.grounding import class_prototypes, embed_object
from orx.exp.suites.s6_recycling.model import S6Episode, S6Object, S6World


def generate_episode(world: S6World, seed: int, sigma: float) -> S6Episode:
    rng = SeedTree(seed).child("s6-gen").rng()
    protos = class_prototypes(world.classes, world.embedding_dim)
    objects: list[S6Object] = []
    for i in range(world.n_objects):
        cls = world.classes[int(rng.integers(0, len(world.classes)))]
        emb = embed_object(protos[cls], sigma, rng)
        objects.append(S6Object(obj_id=f"obj-{seed}-{i}", true_class=cls, embedding=emb))
    return S6Episode(objects=objects)

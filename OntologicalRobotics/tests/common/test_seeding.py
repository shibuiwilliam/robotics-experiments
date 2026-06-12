from orx.common.seeding import SeedTree, deterministic_id


def test_same_path_same_seed() -> None:
    a = SeedTree(7).child("sim").child("noise")
    b = SeedTree(7).child("sim").child("noise")
    assert a.seed() == b.seed()
    assert a.rng().integers(0, 1 << 30) == b.rng().integers(0, 1 << 30)


def test_different_paths_differ() -> None:
    root = SeedTree(7)
    assert root.child("sim").seed() != root.child("perception").seed()
    assert SeedTree(7).seed() != SeedTree(8).seed()


def test_path_not_ambiguous() -> None:
    # ("ab","c") と ("a","bc") が衝突しないこと
    assert SeedTree(1).child("ab").child("c").seed() != SeedTree(1).child("a").child("bc").seed()


def test_deterministic_id() -> None:
    r1 = SeedTree(7).child("anchor").rng()
    r2 = SeedTree(7).child("anchor").rng()
    assert deterministic_id(r1) == deterministic_id(r2)
    assert deterministic_id(r1) != deterministic_id(r1)  # 連番は異なる

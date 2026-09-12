import random

import pytest

from gtwm.utils.seed import seed_everything

pytestmark = pytest.mark.unit


def test_seed_everything_is_reproducible() -> None:
    seed_everything(42)
    first = [random.random() for _ in range(5)]
    seed_everything(42)
    second = [random.random() for _ in range(5)]
    assert first == second

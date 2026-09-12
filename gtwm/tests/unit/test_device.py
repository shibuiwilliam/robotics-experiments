import pytest

from gtwm.utils.device import get_device

pytestmark = pytest.mark.unit


def test_get_device_returns_valid_choice() -> None:
    assert get_device() in {"mps", "cpu"}

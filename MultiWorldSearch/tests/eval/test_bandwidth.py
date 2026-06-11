"""Tests for virtual edge<->cloud bandwidth meter."""

from mws.eval.bandwidth import BandwidthMeter


def test_embedding_request_tracking() -> None:
    bw = BandwidthMeter()
    bw.record_embedding_request("hello world", embedding_dims=128)

    summary = bw.summary()
    assert summary["total_upload_bytes"] > 0
    assert summary["total_download_bytes"] > 0
    assert summary["total_bandwidth_bytes"] == (
        summary["total_upload_bytes"] + summary["total_download_bytes"]
    )


def test_llm_request_tracking() -> None:
    bw = BandwidthMeter()
    bw.record_llm_request(prompt_chars=500, response_chars=200)

    summary = bw.summary()
    assert summary["total_upload_bytes"] > 0
    assert summary["total_download_bytes"] > 0


def test_multiple_requests_accumulate() -> None:
    bw = BandwidthMeter()
    bw.record_embedding_request("text one", embedding_dims=128)
    bw.record_embedding_request("text two", embedding_dims=128)

    summary = bw.summary()
    assert summary["n_requests"] == 2


def test_empty_meter() -> None:
    bw = BandwidthMeter()
    summary = bw.summary()
    assert summary["total_bandwidth_bytes"] == 0
    assert summary["n_requests"] == 0


def test_bandwidth_splits_real_and_virtual() -> None:
    """IMPROVEMENT M14: real cloud traffic and cost-model (virtual) traffic
    are reported under separate keys; totals stay as sums."""
    from mws.eval.bandwidth import BandwidthMeter

    bw = BandwidthMeter()
    bw.record_llm_request(prompt_chars=100, response_chars=50)  # virtual (default)
    bw.record_llm_request(prompt_chars=100, response_chars=50, real=True)
    s = bw.summary()
    assert s["total_real_bytes"] > 0
    assert s["total_virtual_bytes"] > 0
    assert s["total_real_bytes"] + s["total_virtual_bytes"] == s["total_bandwidth_bytes"]


def test_bandwidth_mock_embedding_is_virtual() -> None:
    from mws.eval.bandwidth import BandwidthMeter

    bw = BandwidthMeter()
    bw.record_embedding_request("hello", 128)  # default: virtual
    assert bw.summary()["total_real_bytes"] == 0

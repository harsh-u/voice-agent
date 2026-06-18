"""Head + tail sampling decision logic (§5)."""
from __future__ import annotations
import hashlib


def head_sample(trace_id: str, sample_rate: float) -> bool:
    """
    Deterministic head sampling by trace_id.
    Returns True if trace should be fully sampled (spans written).
    Always returns True when sample_rate >= 1.0.
    """
    if sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    # Use first 8 hex chars of sha256 → 32-bit int → [0, 1) float
    h = hashlib.sha256(trace_id.encode()).hexdigest()[:8]
    normalized = int(h, 16) / 0xFFFFFFFF
    return normalized < sample_rate


def tail_keep(
    response_latency_ms: float | None,
    slow_threshold_ms: float,
    has_error: bool,
) -> bool:
    """
    Tail-sampling override: always keep full spans for slow or errored traces.
    Returns True if this trace should be kept regardless of head sampling.
    """
    if has_error:
        return True
    if response_latency_ms is not None and response_latency_ms > slow_threshold_ms:
        return True
    return False


def should_write_spans(
    trace_id: str,
    sample_rate: float,
    slow_threshold_ms: float,
    response_latency_ms: float | None = None,
    has_error: bool = False,
) -> tuple[bool, bool]:
    """
    Returns (write_spans: bool, sampled_flag: bool).
    - write_spans: whether to insert spans/turns into the DB
    - sampled_flag: value to store on the traces.sampled column

    Unsampled traces still get a metadata-only traces row (sampled=False)
    so counts/cost remain correct — only spans/turns are dropped.
    """
    # Tail sampling always wins
    if tail_keep(response_latency_ms, slow_threshold_ms, has_error):
        return True, True

    # Head sampling
    if head_sample(trace_id, sample_rate):
        return True, True

    return False, False

"""Framework-independent working-memory compaction helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def compact_working_memory(
    observations: Sequence[Mapping[str, object]],
    evidence_ids: Sequence[str],
    *,
    keep_recent: int = 4,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    """Compress older observations while retaining every referenced evidence ID."""
    normalized = [dict(item) for item in observations]
    if len(normalized) <= keep_recent:
        return normalized, None
    older = normalized[:-keep_recent]
    recent = normalized[-keep_recent:]
    tools = [str(item.get("tool")) for item in older if item.get("tool")]
    summary: dict[str, object] = {
        "memory_layer": "working",
        "compressed_observations": len(older),
        "tools": list(dict.fromkeys(tools)),
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
    }
    return [summary, *recent], summary

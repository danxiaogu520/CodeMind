from codemind.evaluation.retrieval import (
    BenchmarkCase,
    RelevantItem,
    score_case,
    summarize,
)


def test_retrieval_metrics_score_rank_and_recall() -> None:
    case = BenchmarkCase(
        question="where",
        relevant=(RelevantItem("src/a.py", "target"), RelevantItem("src/b.py")),
    )
    result = score_case(
        case,
        [
            {"path": "src/noise.py", "symbol": "noise"},
            {"path": "src/a.py", "symbol": "target"},
            {"path": "src/b.py", "symbol": "anything"},
        ],
        12.0,
    )
    report = summarize([result])

    assert result.recall_at_5 == 1.0
    assert result.reciprocal_rank == 0.5
    assert report["recall_at_10"] == 1.0
    assert report["latency_p95_ms"] == 12.0

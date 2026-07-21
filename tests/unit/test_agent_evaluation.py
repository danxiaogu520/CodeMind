from codemind.evaluation.agent import summarize


def test_agent_benchmark_summary() -> None:
    report = summarize(
        [
            {
                "completed": True,
                "citation_valid": True,
                "expected_path_recall": 1.0,
                "latency_ms": 10.0,
            },
            {
                "completed": False,
                "citation_valid": False,
                "expected_path_recall": 0.0,
                "latency_ms": 20.0,
            },
        ]
    )

    assert report["task_completion_rate"] == 0.5
    assert report["citation_validity"] == 0.5
    assert report["latency_avg_ms"] == 15.0

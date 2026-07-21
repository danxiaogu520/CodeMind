from __future__ import annotations

import pytest

from codemind.interfaces.demo_cli import ApiClient, build_parser


def test_demo_cli_rejects_non_http_api_url() -> None:
    with pytest.raises(ValueError, match="HTTP"):
        ApiClient("file:///tmp/codemind")


def test_demo_cli_has_zero_configuration_end_to_end_defaults() -> None:
    args = build_parser().parse_args(["demo"])

    assert args.base_url == "http://127.0.0.1:8000"
    assert args.source == "/fixtures/sample_python"
    assert args.workflow == "trace_symbol"
    assert args.question == "Trace calls to create_token"

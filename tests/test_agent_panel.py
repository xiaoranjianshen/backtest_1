# -*- coding: utf-8 -*-
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.agent_panel import (
    EXECUTE_RESULT_STATE_KEY,
    PLAN_PROMPT_STATE_KEY,
    PLAN_RESULT_STATE_KEY,
    PLAN_STATE_KEY,
    agent_url,
    build_data_request_payload,
    clear_stale_results_for_prompt,
    normalize_base_url,
    request_agent_json,
    split_csv_text,
)


def test_agent_url_normalizes_base_and_path():
    assert normalize_base_url("http://127.0.0.1:8010/") == "http://127.0.0.1:8010"
    assert agent_url("http://127.0.0.1:8010/", "api/system/health/") == (
        "http://127.0.0.1:8010/api/system/health/"
    )


def test_request_agent_json_reports_unreachable_service():
    response = request_agent_json(
        "GET",
        "http://127.0.0.1:9",
        "/api/system/health/",
        timeout=0.1,
    )

    assert response.ok is False
    assert response.status_code is None
    assert "Cannot reach Backtest Agent" in response.error


def test_clear_stale_results_for_prompt_removes_old_plan_state():
    state = {
        PLAN_PROMPT_STATE_KEY: "show data catalog",
        PLAN_STATE_KEY: {"tool_name": "data.catalog"},
        PLAN_RESULT_STATE_KEY: object(),
        EXECUTE_RESULT_STATE_KEY: object(),
    }

    changed = clear_stale_results_for_prompt("download au2606 daily data", state)

    assert changed is True
    assert PLAN_PROMPT_STATE_KEY not in state
    assert PLAN_STATE_KEY not in state
    assert PLAN_RESULT_STATE_KEY not in state
    assert EXECUTE_RESULT_STATE_KEY not in state


def test_clear_stale_results_for_prompt_keeps_current_plan_state():
    state = {
        PLAN_PROMPT_STATE_KEY: "show data catalog",
        PLAN_STATE_KEY: {"tool_name": "data.catalog"},
    }

    changed = clear_stale_results_for_prompt("show data catalog", state)

    assert changed is False
    assert state[PLAN_STATE_KEY]["tool_name"] == "data.catalog"


def test_build_data_request_payload_for_raw_contract():
    payload, error = build_data_request_payload(
        symbols_text="au2606",
        data_type="all",
        freq="1d",
        start_date="2025-01-01",
        end_date="2026-06-22",
        output_format="csv",
        limit=10000,
    )

    assert error == ""
    assert payload == {
        "dataset_key": "futures.history",
        "symbols": ["au2606"],
        "start_date": "2025-01-01",
        "end_date": "2026-06-22",
        "freq": "1d",
        "data_type": "all",
        "output_format": "csv",
        "limit": 10000,
    }


def test_build_data_request_payload_rejects_product_for_raw_contract():
    payload, error = build_data_request_payload(
        symbols_text="au",
        data_type="all",
        freq="1d",
        start_date="2025-01-01",
        end_date="2026-06-22",
        output_format="csv",
        limit=10000,
    )

    assert payload == {}
    assert "具体合约" in error


def test_build_data_request_payload_rejects_contract_for_main_series():
    payload, error = build_data_request_payload(
        symbols_text="au2606",
        data_type="main",
        freq="1d",
        start_date="2025-01-01",
        end_date="2026-06-22",
        output_format="csv",
        limit=10000,
    )

    assert payload == {}
    assert "品种代码" in error


def test_split_csv_text_normalizes_symbols():
    assert split_csv_text(" AU2606, rb2605 ,,") == ["au2606", "rb2605"]

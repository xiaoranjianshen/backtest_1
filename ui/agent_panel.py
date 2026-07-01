# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any

import streamlit as st
import streamlit.components.v1 as components


DEFAULT_AGENT_BASE_URL = "http://127.0.0.1:8010"
BASE_URL_STATE_KEY = "backtest_agent_base_url"
PLAN_STATE_KEY = "backtest_agent_plan"
PLAN_PROMPT_STATE_KEY = "backtest_agent_plan_prompt"
PLAN_RESULT_STATE_KEY = "backtest_agent_plan_result"
EXECUTE_RESULT_STATE_KEY = "backtest_agent_execute_result"
DATA_REQUEST_JOB_ID_STATE_KEY = "backtest_agent_data_request_job_id"
DATA_REQUEST_SUBMIT_STATE_KEY = "backtest_agent_data_request_submit_result"
DATA_REQUEST_JOB_STATE_KEY = "backtest_agent_data_request_job_result"

TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_JOB_STATUSES = {"pending", "running"}
DATA_TYPE_OPTIONS = {
    "单合约原始数据": "all",
    "主力连续": "main",
    "复权主力连续": "main_adj",
    "指数连续": "index",
}
FREQ_OPTIONS = {
    "日线": "1d",
    "5分钟": "5m",
    "1分钟": "1m",
    "Tick": "tick",
}
VALID_FREQ_BY_DATA_TYPE = {
    "all": ["1d", "5m", "1m", "tick"],
    "main": ["1d", "5m", "1m", "tick"],
    "main_adj": ["1m"],
    "index": ["1d", "5m", "1m"],
}
OUTPUT_FORMAT_OPTIONS = ["csv", "parquet"]


@dataclass(slots=True)
class AgentApiResponse:
    ok: bool
    status_code: int | None
    data: dict[str, Any]
    error: str = ""


def normalize_base_url(value: str | None) -> str:
    text = (value or DEFAULT_AGENT_BASE_URL).strip()
    return text.rstrip("/") or DEFAULT_AGENT_BASE_URL


def agent_url(base_url: str, path: str) -> str:
    clean_base = normalize_base_url(base_url)
    clean_path = "/" + path.lstrip("/")
    return clean_base + clean_path


def request_agent_json(
    method: str,
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> AgentApiResponse:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(
        agent_url(base_url, path),
        data=body,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return AgentApiResponse(
                ok=200 <= int(response.status) < 300,
                status_code=int(response.status),
                data=_loads_response_json(raw),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return AgentApiResponse(
            ok=False,
            status_code=int(exc.code),
            data=_loads_response_json(raw),
            error=_extract_error(raw) or str(exc),
        )
    except urllib.error.URLError as exc:
        return AgentApiResponse(
            ok=False,
            status_code=None,
            data={},
            error=f"Cannot reach Backtest Agent at {normalize_base_url(base_url)}: {exc.reason}",
        )
    except OSError as exc:
        return AgentApiResponse(
            ok=False,
            status_code=None,
            data={},
            error=f"Cannot call Backtest Agent at {normalize_base_url(base_url)}: {exc}",
        )


def render_agent_panel() -> None:
    st.markdown("### Agent 助手")
    st.caption(
        "这个板块直接连接 backtest_agent 服务。普通问题先点 Plan；需要真正创建任务或执行工具时再点 Execute。"
    )

    base_url = _base_url_input()
    _render_connection_bar(base_url)

    data_tab, chat_tab, workbench_tab = st.tabs(["获取数据", "Agent 对话", "完整 Workbench"])
    with data_tab:
        _render_data_request_card(base_url)
    with chat_tab:
        _render_agent_chat(base_url)
    with workbench_tab:
        _render_embedded_workbench(base_url)


def _render_agent_chat(base_url: str) -> None:
    _render_quick_prompts()

    prompt = st.text_area(
        "Prompt",
        key="backtest_agent_prompt",
        height=120,
        value=st.session_state.get("backtest_agent_prompt", "这个系统能干吗？"),
    )
    if clear_stale_results_for_prompt(prompt, st.session_state):
        st.info("Prompt changed. Click Plan to generate a fresh plan.")

    col_mode, col_manifest, col_confirm = st.columns([1, 1, 1])
    planner_mode = col_mode.selectbox(
        "Planner",
        ["rules", "llm"],
        index=0,
        key="backtest_agent_planner_mode",
    )
    include_manifest = col_manifest.checkbox(
        "Show manifest",
        value=False,
        key="backtest_agent_include_manifest",
    )
    confirm = col_confirm.checkbox(
        "Confirm execution",
        value=False,
        key="backtest_agent_confirm",
    )

    col_plan, col_execute, col_full = st.columns([1, 1, 2])
    if col_plan.button("Plan", type="primary", use_container_width=True, key="backtest_agent_chat_plan"):
        _run_plan(base_url, prompt, planner_mode, include_manifest)
    if col_execute.button("Execute", use_container_width=True, key="backtest_agent_chat_execute"):
        _run_execute(base_url, prompt, planner_mode, include_manifest, confirm)
    col_full.link_button("Open full Agent Workbench", agent_url(base_url, "/workbench/"))

    _render_plan_state()
    _render_execute_state()


def _render_embedded_workbench(base_url: str) -> None:
    st.caption("如果嵌入区没有显示，请点击 Agent 对话里的 Open full Agent Workbench。")
    components.iframe(agent_url(base_url, "/workbench/?embed=1"), height=860, scrolling=True)


def _render_data_request_card(base_url: str) -> None:
    st.markdown("#### 获取数据")
    st.caption("常用数据导出可以直接用这张卡片完成；复杂或不确定的需求再切到 Agent 对话。")

    data_type_label = st.selectbox(
        "获取方式",
        list(DATA_TYPE_OPTIONS),
        index=0,
        key="backtest_agent_data_type_label",
        help="单合约原始数据用于 au2606 这类具体合约；主力/指数用于 au、rb 这类品种代码。",
    )
    data_type = DATA_TYPE_OPTIONS[data_type_label]
    freq_labels = [
        label
        for label, value in FREQ_OPTIONS.items()
        if value in VALID_FREQ_BY_DATA_TYPE[data_type]
    ]

    top_cols = st.columns([1.2, 1, 1])
    default_symbol = "au2606" if data_type == "all" else "au"
    symbol_key = f"backtest_agent_data_symbols_{data_type}"
    if symbol_key not in st.session_state:
        st.session_state[symbol_key] = default_symbol
    symbols_text = top_cols[0].text_input(
        "品种/合约",
        key=symbol_key,
        help=(
            "单合约原始数据请填 au2606、rb2605；主力/指数请填 au、rb。"
        ),
    )
    freq_label = top_cols[1].selectbox(
        "周期",
        freq_labels,
        index=0,
        key=f"backtest_agent_data_freq_{data_type}",
    )
    output_format = top_cols[2].selectbox(
        "输出格式",
        OUTPUT_FORMAT_OPTIONS,
        index=0,
        key="backtest_agent_data_output_format",
    )

    current_year_start = date(date.today().year, 1, 1)
    date_cols = st.columns([1, 1, 1])
    start_date = date_cols[0].date_input(
        "开始日期",
        value=current_year_start,
        key="backtest_agent_data_start_date",
    )
    end_date = date_cols[1].date_input(
        "结束日期",
        value=date.today(),
        key="backtest_agent_data_end_date",
    )
    limit = date_cols[2].number_input(
        "最大行数",
        min_value=1,
        max_value=1_000_000,
        value=10_000,
        step=1000,
        key="backtest_agent_data_limit",
        help="后端默认最多 10000 行；需要更大导出时再调高。",
    )
    fields_text = st.text_input(
        "字段（可选）",
        value="",
        key="backtest_agent_data_fields",
        placeholder="例如 open,high,low,close；留空表示默认字段",
    )

    submit_col, refresh_col, clear_col = st.columns([1, 1, 2])
    if submit_col.button(
        "提交数据任务",
        type="primary",
        use_container_width=True,
        key="backtest_agent_data_submit",
    ):
        payload, error = build_data_request_payload(
            symbols_text=symbols_text,
            data_type=data_type,
            freq=FREQ_OPTIONS[freq_label],
            start_date=str(start_date),
            end_date=str(end_date),
            output_format=output_format,
            limit=int(limit),
            fields_text=fields_text,
        )
        if error:
            st.error(error)
        else:
            _submit_data_request(base_url, payload)
    if refresh_col.button("刷新任务状态", use_container_width=True, key="backtest_agent_data_refresh"):
        _refresh_data_job(base_url)
    if clear_col.button("清空当前数据任务", use_container_width=True, key="backtest_agent_data_clear"):
        _set_data_job_id("")
        st.session_state.pop(DATA_REQUEST_SUBMIT_STATE_KEY, None)
        st.session_state.pop(DATA_REQUEST_JOB_STATE_KEY, None)
        st.rerun()

    _render_data_request_submit_state()
    _render_data_job_summary(base_url)


def build_data_request_payload(
    *,
    symbols_text: str,
    data_type: str,
    freq: str,
    start_date: str,
    end_date: str,
    output_format: str,
    limit: int,
    fields_text: str = "",
) -> tuple[dict[str, Any], str]:
    symbols = split_csv_text(symbols_text)
    fields = split_csv_text(fields_text)
    if not symbols:
        return {}, "请至少填写一个品种或合约。"
    if start_date > end_date:
        return {}, "开始日期不能晚于结束日期。"
    if freq not in VALID_FREQ_BY_DATA_TYPE.get(data_type, []):
        return {}, f"{data_type} 暂不支持 {freq} 周期。"
    if output_format not in OUTPUT_FORMAT_OPTIONS:
        return {}, f"暂不支持输出格式：{output_format}"

    has_month_digits = [_has_contract_month(symbol) for symbol in symbols]
    if data_type == "all" and not all(has_month_digits):
        return {}, "单合约原始数据需要填写具体合约，例如 au2606，而不是 au。"
    if data_type != "all" and any(has_month_digits):
        return {}, "主力/复权主力/指数只需要填写品种代码，例如 au，不要填写 au2606。"

    payload: dict[str, Any] = {
        "dataset_key": "futures.history",
        "symbols": symbols,
        "start_date": start_date,
        "end_date": end_date,
        "freq": freq,
        "data_type": data_type,
        "output_format": output_format,
        "limit": int(limit),
    }
    if fields:
        payload["fields"] = fields
    return payload, ""


def split_csv_text(value: str) -> list[str]:
    return [item.strip().lower() for item in str(value or "").split(",") if item.strip()]


def _has_contract_month(symbol: str) -> bool:
    raw_symbol = str(symbol).strip().rsplit(".", 1)[-1]
    return bool(re.search(r"\d", raw_symbol))


def _submit_data_request(base_url: str, payload: dict[str, Any]) -> None:
    result = request_agent_json(
        "POST",
        base_url,
        "/api/agent/execute/",
        payload={"tool_name": "data.request", "payload": payload, "confirm": True},
        timeout=30.0,
    )
    st.session_state[DATA_REQUEST_SUBMIT_STATE_KEY] = result
    if not result.ok:
        return
    job_id = _extract_job_id(result.data)
    if not job_id:
        return
    _set_data_job_id(job_id)
    with st.spinner("数据任务已提交，正在等待结果..."):
        _poll_data_job_until_terminal(base_url, job_id, timeout_seconds=15.0)


def _render_data_request_submit_state() -> None:
    result = st.session_state.get(DATA_REQUEST_SUBMIT_STATE_KEY)
    if not isinstance(result, AgentApiResponse):
        return
    if result.ok:
        job_id = _extract_job_id(result.data)
        if job_id:
            st.success(f"数据任务已创建：{job_id}")
        else:
            st.success("数据任务已提交。")
    else:
        st.error(result.error or f"数据任务提交失败：{result.status_code}")
        with st.expander("提交失败详情", expanded=False):
            st.json(result.data)


def _render_data_job_summary(base_url: str) -> None:
    job_id = _get_data_job_id()
    if not job_id:
        return

    result = _load_data_job_detail(base_url, job_id)
    st.session_state[DATA_REQUEST_JOB_STATE_KEY] = result
    if not result.ok:
        st.warning(result.error or f"无法读取任务状态：{job_id}")
        return

    job = result.data
    status = str(job.get("status") or "")
    output = job.get("output_payload") if isinstance(job.get("output_payload"), dict) else {}
    metric_cols = st.columns(4)
    metric_cols[0].metric("状态", status or "-")
    metric_cols[1].metric("行数", str(output.get("row_count", "-")))
    metric_cols[2].metric("文件", job.get("artifact_name") or "-")
    metric_cols[3].metric("任务类型", job.get("kind") or "-")

    if status == "succeeded":
        st.success("数据已生成，可以下载。")
        if job.get("artifact_url"):
            st.link_button(
                "下载数据文件",
                agent_url(base_url, str(job["artifact_url"])),
                use_container_width=True,
            )
        preview = output.get("preview")
        if isinstance(preview, list) and preview:
            st.dataframe(preview, use_container_width=True)
    elif status in ACTIVE_JOB_STATUSES:
        st.info("任务正在排队或运行，页面会自动刷新。")
        _schedule_streamlit_reload(3)
    elif status == "failed":
        st.error(job.get("error_message") or "任务失败，展开详情查看完整信息。")
    elif status == "cancelled":
        st.warning("任务已取消。")

    with st.expander("任务详情 / 完整日志", expanded=False):
        st.json(job)


def _load_data_job_detail(base_url: str, job_id: str) -> AgentApiResponse:
    return request_agent_json("GET", base_url, f"/api/jobs/{job_id}/", timeout=10.0)


def _refresh_data_job(base_url: str) -> None:
    job_id = _get_data_job_id()
    if not job_id:
        st.info("还没有当前数据任务。")
        return
    st.session_state[DATA_REQUEST_JOB_STATE_KEY] = _load_data_job_detail(base_url, job_id)


def _poll_data_job_until_terminal(
    base_url: str,
    job_id: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = _load_data_job_detail(base_url, job_id)
        st.session_state[DATA_REQUEST_JOB_STATE_KEY] = result
        status = str(result.data.get("status") or "").lower() if result.ok else ""
        if status in TERMINAL_JOB_STATUSES or time.monotonic() >= deadline:
            return
        time.sleep(1.0)


def _extract_job_id(data: dict[str, Any]) -> str:
    tool_call = data.get("tool_call") if isinstance(data, dict) else None
    tool_data = tool_call.get("data") if isinstance(tool_call, dict) else None
    job_id = tool_data.get("job_id") if isinstance(tool_data, dict) else None
    return str(job_id or "").strip()


def _set_data_job_id(job_id: str) -> None:
    if job_id:
        st.session_state[DATA_REQUEST_JOB_ID_STATE_KEY] = job_id
    else:
        st.session_state.pop(DATA_REQUEST_JOB_ID_STATE_KEY, None)
    try:
        if job_id:
            st.query_params["data_job_id"] = job_id
        else:
            st.query_params.pop("data_job_id", None)
    except Exception:
        return


def _get_data_job_id() -> str:
    job_id = str(st.session_state.get(DATA_REQUEST_JOB_ID_STATE_KEY, "") or "").strip()
    if job_id:
        return job_id
    try:
        query_value = st.query_params.get("data_job_id", "")
    except Exception:
        query_value = ""
    if isinstance(query_value, list):
        query_value = query_value[0] if query_value else ""
    return str(query_value or "").strip()


def _schedule_streamlit_reload(delay_seconds: int) -> None:
    interval_ms = max(1, int(delay_seconds)) * 1000
    components.html(
        f"""
        <script>
          setTimeout(() => {{
            window.parent.location.reload();
          }}, {interval_ms});
        </script>
        """,
        height=0,
    )


def _loads_response_json(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {"value": data}


def clear_stale_results_for_prompt(prompt: str, state: dict[str, Any]) -> bool:
    previous_prompt = state.get(PLAN_PROMPT_STATE_KEY)
    if not previous_prompt or previous_prompt == prompt:
        return False
    state.pop(PLAN_STATE_KEY, None)
    state.pop(PLAN_RESULT_STATE_KEY, None)
    state.pop(EXECUTE_RESULT_STATE_KEY, None)
    state.pop(PLAN_PROMPT_STATE_KEY, None)
    return True


def _extract_error(raw: str) -> str:
    data = _loads_response_json(raw)
    detail = data.get("detail")
    if isinstance(detail, str):
        return detail
    if detail:
        return json.dumps(detail, ensure_ascii=False)
    return ""


def _base_url_input() -> str:
    default_url = os.getenv("BACKTEST_AGENT_BASE_URL", DEFAULT_AGENT_BASE_URL)
    if BASE_URL_STATE_KEY not in st.session_state:
        st.session_state[BASE_URL_STATE_KEY] = default_url
    return normalize_base_url(
        st.text_input(
            "Agent service URL",
            key=BASE_URL_STATE_KEY,
            help="默认是本机 backtest_agent Django 服务。",
        )
    )


def _render_connection_bar(base_url: str) -> None:
    col_status, col_check = st.columns([3, 1])
    col_status.markdown(f"当前连接：`{normalize_base_url(base_url)}`")
    if not col_check.button("Live Check", use_container_width=True, key="backtest_agent_live_check"):
        return
    result = request_agent_json("GET", base_url, "/api/system/health/?live=1", timeout=5.0)
    if result.ok:
        status = result.data.get("status", "unknown")
        if status == "ok":
            st.success("Agent 服务正常，数据后端 live check 通过。")
        else:
            st.warning(f"Agent 服务可访问，但系统状态是 {status}。")
        with st.expander("System health payload", expanded=False):
            st.json(result.data)
    else:
        st.error(result.error or "Agent 服务不可访问。请先启动 backtest_agent。")
        st.code(
            "cd C:\\Users\\tnori\\PyCharmMiscProject\\backtest_agent\n"
            ".\\.venv\\Scripts\\python.exe manage.py runserver 127.0.0.1:8010",
            language="powershell",
        )


def _render_quick_prompts() -> None:
    st.markdown("#### 快速开始")
    examples = [
        "这个系统能干吗？",
        "show data catalog",
        "我要查南华指数从2024年到现在的数据",
        "show available strategies",
        "帮我搭一个双均线策略草稿",
    ]
    columns = st.columns(len(examples))
    for index, example in enumerate(examples):
        if columns[index].button(example, key=f"backtest_agent_example_{index}"):
            st.session_state["backtest_agent_prompt"] = example
            st.session_state.pop(PLAN_STATE_KEY, None)
            st.session_state.pop(PLAN_RESULT_STATE_KEY, None)
            st.session_state.pop(EXECUTE_RESULT_STATE_KEY, None)
            st.rerun()


def _run_plan(base_url: str, prompt: str, planner_mode: str, include_manifest: bool) -> None:
    payload = {
        "prompt": prompt,
        "planner_mode": planner_mode,
        "include_manifest": include_manifest,
    }
    result = request_agent_json("POST", base_url, "/api/agent/plan/", payload=payload)
    st.session_state[PLAN_RESULT_STATE_KEY] = result
    if result.ok:
        st.session_state[PLAN_STATE_KEY] = result.data.get("plan") or {}
        st.session_state[PLAN_PROMPT_STATE_KEY] = prompt
        st.session_state.pop(EXECUTE_RESULT_STATE_KEY, None)


def _run_execute(
    base_url: str,
    prompt: str,
    planner_mode: str,
    include_manifest: bool,
    confirm: bool,
) -> None:
    plan = st.session_state.get(PLAN_STATE_KEY)
    if not plan or st.session_state.get(PLAN_PROMPT_STATE_KEY) != prompt:
        _run_plan(base_url, prompt, planner_mode, include_manifest)
        plan = st.session_state.get(PLAN_STATE_KEY)
    if not isinstance(plan, dict):
        return
    tool_name = plan.get("tool_name")
    if not tool_name:
        st.session_state[EXECUTE_RESULT_STATE_KEY] = AgentApiResponse(
            ok=True,
            status_code=200,
            data={
                "answer": plan.get("answer", ""),
                "suggested_actions": plan.get("suggested_actions", []),
                "plan": plan,
            },
        )
        return
    payload = {
        "tool_name": tool_name,
        "payload": plan.get("payload") or {},
        "confirm": bool(confirm),
    }
    st.session_state[EXECUTE_RESULT_STATE_KEY] = request_agent_json(
        "POST",
        base_url,
        "/api/agent/execute/",
        payload=payload,
    )


def _render_plan_state() -> None:
    result = st.session_state.get(PLAN_RESULT_STATE_KEY)
    if not isinstance(result, AgentApiResponse):
        return
    st.markdown("#### Plan")
    if not result.ok:
        st.error(result.error or f"Plan failed with status {result.status_code}")
        st.json(result.data)
        return
    plan = result.data.get("plan") or {}
    if plan.get("answer"):
        st.info(str(plan["answer"]))
    metric_cols = st.columns(4)
    metric_cols[0].metric("Tool", plan.get("tool_name") or "answer only")
    metric_cols[1].metric("Execute", str(plan.get("execute", False)))
    metric_cols[2].metric("Confidence", str(plan.get("confidence", "-")))
    metric_cols[3].metric("Planner", plan.get("planner", "-"))
    if plan.get("reasoning"):
        st.caption(f"Reasoning: {plan['reasoning']}")
    if plan.get("suggested_actions"):
        st.markdown("Suggested actions:")
        for action in plan["suggested_actions"]:
            st.markdown(f"- {action}")
    with st.expander("Plan JSON", expanded=False):
        st.json(result.data)


def _render_execute_state() -> None:
    result = st.session_state.get(EXECUTE_RESULT_STATE_KEY)
    if not isinstance(result, AgentApiResponse):
        return
    st.markdown("#### Execute Result")
    if result.ok:
        if result.data.get("answer"):
            st.success(str(result.data["answer"]))
        st.json(result.data)
    else:
        if result.status_code == 409:
            st.warning("这个工具需要勾选 Confirm execution 后才能执行。")
        else:
            st.error(result.error or f"Execute failed with status {result.status_code}")
        st.json(result.data)

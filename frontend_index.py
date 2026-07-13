# -*- coding: utf-8 -*-
import os
import json
import socket
import subprocess
import sys
import time
import webbrowser
import pandas as pd
from plotly.offline import get_plotlyjs


def _load_report_javascript() -> tuple[str, str, str]:
    """Load local JavaScript so generated reports work without internet access."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    vendor_dir = os.path.join(project_root, "web_static", "vendor")
    assets = []
    for filename in ("html2canvas.min.js", "jspdf.umd.min.js"):
        path = os.path.join(vendor_dir, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"报告依赖缺失: {path}")
        with open(path, "r", encoding="utf-8") as handle:
            assets.append(handle.read())
    return get_plotlyjs(), assets[0], assets[1]


def _is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _ensure_streamlit_config_ui(port: int = 8501):
    project_root = os.path.dirname(os.path.abspath(__file__))
    app_path = os.path.join(project_root, "ui", "app.py")
    if not os.path.exists(app_path):
        print(f"[Engine Front] 配置页面未找到，跳过启动: {app_path}")
        return

    if _is_port_open(port):
        print(f"[Engine Front] 配置页面服务已运行: http://localhost:{port}")
        return

    stdout_path = os.path.join(project_root, "streamlit_ui_stdout.log")
    stderr_path = os.path.join(project_root, "streamlit_ui_stderr.log")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        stdout = open(stdout_path, "a", encoding="utf-8", errors="replace")
        stderr = open(stderr_path, "a", encoding="utf-8", errors="replace")
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                app_path,
                "--server.port",
                str(port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            cwd=project_root,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    except Exception as exc:
        print(f"[Engine Front] 配置页面启动失败: {exc}")
        return

    for _ in range(20):
        if _is_port_open(port):
            print(f"[Engine Front] 配置页面服务已启动: http://localhost:{port}")
            return
        time.sleep(0.5)

    print(f"[Engine Front] 配置页面暂未就绪，可查看日志: {stderr_path}")


def _write_active_report_config(analyzer):
    config = getattr(analyzer, "run_config", None)
    if not config:
        return

    project_root = os.path.dirname(os.path.abspath(__file__))
    runtime_dir = os.path.join(project_root, "ui", ".runtime")
    os.makedirs(runtime_dir, exist_ok=True)
    config_path = os.path.join(runtime_dir, "active_report_config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def build_html_dashboard(analyzer, open_browser=True, start_config_ui=True):
    plotly_js, html2canvas_js, jspdf_js = _load_report_javascript()
    if analyzer is None:
        print("[Engine Front] 未接收到有效分析数据，已终止看板生成。")
        return None
    print("[Engine Front] 正在生成回测看板...")
    _write_active_report_config(analyzer)

    # 1. 从 analyzer 提取基础图表 HTML 代码块。
    html_metrics = analyzer.get_metrics_table_html()
    html_params = analyzer.get_params_table_html()
    html_fig_eq = analyzer.get_equity_html_div()
    html_fig_cum = analyzer.get_cum_pnl_html_div()
    html_fig_nv_bench = analyzer.get_net_value_with_benchmark_html_div()
    html_fig_dd = analyzer.get_rolling_drawdown_html_div()
    html_fig_leverage = analyzer.get_leverage_and_position_html_div()
    html_fig_margin_utilization = analyzer.get_margin_utilization_html_div()
    html_fig_pnl_bar = analyzer.get_multi_asset_pnl_bar_html_div()
    html_fig_holding_pie = analyzer.get_holding_period_pie_html_div()
    html_fig_turnover_pie = analyzer.get_turnover_pie_html_div()
    html_fig_pnl_curves = analyzer.get_multi_asset_pnl_curves_html_div()
    html_fig_pnl_dist = analyzer.get_pnl_distribution_html_div()
    html_fig_period_ret = analyzer.get_period_returns_html_div()
    html_signal_diagnostics = analyzer.get_signal_diagnostics_html_div() if hasattr(analyzer, 'get_signal_diagnostics_html_div') else ""

    # 2. 提取并组装交易复盘区。
    replay_dicts = analyzer.get_replay_charts_dict() if hasattr(analyzer, 'get_replay_charts_dict') else {}
    html_replay_section = ""
    if replay_dicts:
        buttons_html = ""
        divs_html = ""
        first = True
        for sym, div in replay_dicts.items():
            active_cls = "bg-[#1e3a8a] text-white shadow-md" if first else "bg-gray-100 text-gray-600 hover:bg-gray-200"
            display_style = "block" if first else "none"

            buttons_html += f"""
                <button onclick="switchReplay('{sym}')" id="btn-replay-{sym}" 
                    class="replay-btn px-6 py-2 rounded-full text-sm font-bold transition-all {active_cls}">
                    {sym}
                </button>
            """
            divs_html += f"""
                <div id="replay-content-{sym}" class="replay-content w-full" style="display: {display_style};">
                    {div}
                </div>
            """
            first = False

        html_replay_section = f"""
        <div id="report-replay-section" class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
            <h2 class="text-lg font-bold text-gray-800 border-l-4 border-indigo-600 pl-3 mb-4">交易复盘 (Trade Replay)</h2>
            <div class="flex space-x-3 overflow-x-auto pb-3 mb-2 border-b border-gray-100">
                {buttons_html}
            </div>
            <div class="w-full relative mt-2">
                {divs_html}
            </div>
        </div>
        """

    # 3. 提取交易流水表。
    if hasattr(analyzer, 'match_df') and not analyzer.match_df.empty:
        df_t = analyzer.match_df[
            ['open_time', 'close_time', 'symbol', 'direction', 'volume', 'open_price', 'close_price', 'net_pnl',
             'commission']].copy()
        df_t.columns = ['开仓时间', '平仓时间', '合约', '方向', '手数', '开仓价', '平仓价', '净盈亏', '手续费']
        csv_filename = 'trades_log_full.csv'
        df_t.to_csv(os.path.join(analyzer.output_dir, csv_filename), index=False, encoding='utf-8-sig')

        total_trades = len(df_t)
        df_t_display = df_t.head(1000).copy()
        df_t_display['净盈亏'] = df_t_display['净盈亏'].apply(
            lambda x: f"<span class='{'text-red-500' if x > 0 else 'text-green-500'} font-bold'>{x:.2f}</span>")
        html_table = df_t_display.to_html(index=False, border=0, escape=False,
                                          classes="w-full text-sm text-center text-gray-600 bg-white")
        html_table = html_table.replace('<thead>',
                                        '<thead class="bg-gray-100 text-gray-700 sticky top-0 shadow-sm">').replace(
            '<th>', '<th class="py-3 px-4 text-center whitespace-nowrap">').replace('<td>',
                                                                                    '<td class="py-2 px-4 text-center border-b border-gray-50">').replace(
            'style="text-align: right;"', '')

        html_trades = f"""
            <div class="flex justify-between items-center bg-gray-50 p-4 border-b border-gray-200">
                <div class="text-gray-600 text-sm">共检测到 <span class="font-bold text-gray-800">{total_trades}</span> 条交易明细。为保证网页性能，面板仅渲染前 1000 条。</div>
                <a href="{csv_filename}" download class="flex items-center space-x-2 bg-[#1e3a8a] hover:bg-blue-700 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors shadow-sm">
                    <span>下载全量数据 (CSV)</span>
                </a>
            </div>
            <div class="overflow-y-auto max-h-[500px]">{html_table}</div>
        """
    else:
        html_trades = "<p class='p-4 text-gray-500'>无交易流水</p>"

    # 4. 提取资金流水表。
    df_funds = analyzer.get_fund_flow_df() if hasattr(analyzer, 'get_fund_flow_df') else pd.DataFrame()
    if not df_funds.empty:
        csv_funds_filename = 'fund_flow_full.csv'
        df_funds.to_csv(os.path.join(analyzer.output_dir, csv_funds_filename), index=False, encoding='utf-8-sig')
        total_funds = len(df_funds)
        df_f_display = df_funds.head(1000).copy()
        df_f_display['累计盈亏'] = df_f_display['累计盈亏'].apply(
            lambda x: f"<span class='{'text-red-500' if float(x) > 0 else 'text-green-500'} font-bold'>{x:.2f}</span>")
        html_table_funds = df_f_display.to_html(index=False, border=0, escape=False,
                                                classes="w-full text-sm text-center text-gray-600 bg-white")
        html_table_funds = html_table_funds.replace('<thead>',
                                                    '<thead class="bg-gray-100 text-gray-700 sticky top-0 shadow-sm">').replace(
            '<th>', '<th class="py-3 px-4 text-center whitespace-nowrap">').replace('<td>',
                                                                                    '<td class="py-2 px-4 text-center border-b border-gray-50">').replace(
            'style="text-align: right;"', '')

        html_funds = f"""
            <div class="flex justify-between items-center bg-gray-50 p-4 border-b border-gray-200">
                <div class="text-gray-600 text-sm">共检测到 <span class="font-bold text-gray-800">{total_funds}</span> 条资金流记录。为保证网页性能，面板仅渲染前 1000 条。</div>
                <a href="{csv_funds_filename}" download class="flex items-center space-x-2 bg-teal-600 hover:bg-teal-700 text-white px-5 py-2 rounded-lg text-sm font-medium transition-colors shadow-sm">
                    <span>下载资金流表 (CSV)</span>
                </a>
            </div>
            <div class="overflow-y-auto max-h-[500px]">{html_table_funds}</div>
        """
    else:
        html_funds = "<p class='p-4 text-gray-500'>无资金流数据</p>"

    # 5. 组装 HTML 报告。
    html_template = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>{analyzer.strategy_name} - Backtest</title>
        <script>{plotly_js}</script>
        <script>{html2canvas_js}</script>
        <script>{jspdf_js}</script>
        <style>
            /* Self-contained utility styles used by the generated report. */
            *, *::before, *::after {{ box-sizing: border-box; }}
            html, body {{ margin: 0; max-width: 100%; overflow-x: hidden; }}
            body {{ background-color: #f3f4f6; }}
            .min-h-screen {{ min-height: 100vh; }}
            .w-full {{ width: 100%; }}
            .max-w-screen-2xl {{ max-width: 1536px; }}
            .mx-auto {{ margin-left: auto; margin-right: auto; }}
            .flex {{ display: flex; }}
            .grid {{ display: grid; }}
            .grid-cols-1 {{ grid-template-columns: minmax(0, 1fr); }}
            .grid-cols-2 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
            .grid > * {{ min-width: 0; }}
            .hidden {{ display: none; }}
            .block {{ display: block; }}
            .items-end {{ align-items: flex-end; }}
            .items-center {{ align-items: center; }}
            .justify-between {{ justify-content: space-between; }}
            .justify-center {{ justify-content: center; }}
            .flex-col {{ flex-direction: column; }}
            .flex-wrap {{ flex-wrap: wrap; }}
            .overflow-hidden {{ overflow: hidden; }}
            .overflow-x-auto {{ overflow-x: auto; }}
            .overflow-y-auto {{ overflow-y: auto; }}
            .relative {{ position: relative; }}
            .sticky {{ position: sticky; }}
            .top-0 {{ top: 0; }}
            .p-3 {{ padding: 0.75rem; }}
            .p-4 {{ padding: 1rem; }}
            .p-6 {{ padding: 1.5rem; }}
            .px-5 {{ padding-left: 1.25rem; padding-right: 1.25rem; }}
            .px-6 {{ padding-left: 1.5rem; padding-right: 1.5rem; }}
            .px-8 {{ padding-left: 2rem; padding-right: 2rem; }}
            .py-2 {{ padding-top: 0.5rem; padding-bottom: 0.5rem; }}
            .py-3 {{ padding-top: 0.75rem; padding-bottom: 0.75rem; }}
            .pt-5 {{ padding-top: 1.25rem; }}
            .pb-3 {{ padding-bottom: 0.75rem; }}
            .pb-4 {{ padding-bottom: 1rem; }}
            .pl-3 {{ padding-left: 0.75rem; }}
            .mt-2 {{ margin-top: 0.5rem; }}
            .mt-3 {{ margin-top: 0.75rem; }}
            .mt-4 {{ margin-top: 1rem; }}
            .mb-2 {{ margin-bottom: 0.5rem; }}
            .mb-4 {{ margin-bottom: 1rem; }}
            .gap-3 {{ gap: 0.75rem; }}
            .gap-6 {{ gap: 1.5rem; }}
            .space-x-1 > * + * {{ margin-left: 0.25rem; }}
            .space-x-2 > * + * {{ margin-left: 0.5rem; }}
            .space-x-3 > * + * {{ margin-left: 0.75rem; }}
            .space-y-4 > * + * {{ margin-top: 1rem; }}
            .space-y-6 > * + * {{ margin-top: 1.5rem; }}
            .pnl-curve-mode, .pnl-curve-sector-toggle {{
                border: 1px solid #d1d5db; background: #fff; color: #374151;
                border-radius: 6px; padding: 6px 10px; cursor: pointer;
            }}
            .pnl-curve-mode.is-active {{ background: #1e3a8a; color: #fff; border-color: #1e3a8a; }}
            .pnl-curve-sector-toggle.is-muted {{ opacity: 0.42; text-decoration: line-through; }}
            .bg-white {{ background-color: #ffffff; }}
            .bg-gray-50 {{ background-color: #f9fafb; }}
            .bg-gray-100 {{ background-color: #f3f4f6; }}
            .bg-teal-50 {{ background-color: #f0fdfa; }}
            .bg-blue-700 {{ background-color: #1d4ed8; }}
            .bg-teal-600 {{ background-color: #0d9488; }}
            .bg-teal-700 {{ background-color: #0f766e; }}
            .bg-red-50 {{ background-color: #fef2f2; }}
            [class*="bg-[#1e3a8a]"] {{ background-color: #1e3a8a; }}
            [class*="bg-white/95"] {{ background-color: rgba(255, 255, 255, 0.95); }}
            [class*="bg-white/20"] {{ background-color: rgba(255, 255, 255, 0.2); }}
            [class*="bg-[#1e3a8a]/5"] {{ background-color: rgba(30, 58, 138, 0.05); }}
            .text-white {{ color: #ffffff; }}
            .text-blue-100 {{ color: #dbeafe; }}
            .text-blue-200 {{ color: #bfdbfe; }}
            .text-gray-400 {{ color: #9ca3af; }}
            .text-gray-500 {{ color: #6b7280; }}
            .text-gray-600 {{ color: #4b5563; }}
            .text-gray-700 {{ color: #374151; }}
            .text-gray-800 {{ color: #1f2937; }}
            .text-red-500 {{ color: #ef4444; }}
            .text-green-500 {{ color: #22c55e; }}
            [class*="text-[#1e3a8a]"] {{ color: #1e3a8a; }}
            .text-xs {{ font-size: 0.75rem; line-height: 1rem; }}
            .text-sm {{ font-size: 0.875rem; line-height: 1.25rem; }}
            .text-lg {{ font-size: 1.125rem; line-height: 1.75rem; }}
            .text-3xl {{ font-size: 1.875rem; line-height: 2.25rem; }}
            .font-medium {{ font-weight: 500; }}
            .font-bold {{ font-weight: 700; }}
            .tracking-wider {{ letter-spacing: 0.05em; }}
            .text-center {{ text-align: center; }}
            .whitespace-nowrap {{ white-space: nowrap; }}
            .rounded-lg {{ border-radius: 0.5rem; }}
            .rounded-xl {{ border-radius: 0.75rem; }}
            .rounded-full {{ border-radius: 9999px; }}
            .rounded-t-lg {{ border-top-left-radius: 0.5rem; border-top-right-radius: 0.5rem; }}
            .border {{ border-width: 1px; border-style: solid; }}
            .border-b {{ border-bottom-width: 1px; border-bottom-style: solid; }}
            .border-r {{ border-right-width: 1px; border-right-style: solid; }}
            .border-l-4 {{ border-left-width: 4px; border-left-style: solid; }}
            .border-gray-50 {{ border-color: #f9fafb; }}
            .border-gray-100 {{ border-color: #f3f4f6; }}
            .border-gray-200 {{ border-color: #e5e7eb; }}
            .border-blue-600 {{ border-color: #2563eb; }}
            .border-indigo-600 {{ border-color: #4f46e5; }}
            .border-red-500 {{ border-color: #ef4444; }}
            .border-red-600 {{ border-color: #dc2626; }}
            .border-emerald-500 {{ border-color: #10b981; }}
            .border-purple-600 {{ border-color: #9333ea; }}
            .border-orange-500 {{ border-color: #f97316; }}
            .border-teal-500 {{ border-color: #14b8a6; }}
            .border-slate-900 {{ border-color: #0f172a; }}
            [class*="border-[#1e3a8a]"] {{ border-color: #1e3a8a; }}
            .shadow-sm {{ box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}
            .shadow-md {{ box-shadow: 0 4px 6px -1px rgba(0,0,0,0.10), 0 2px 4px -2px rgba(0,0,0,0.10); }}
            .shadow-lg {{ box-shadow: 0 10px 15px -3px rgba(0,0,0,0.10), 0 4px 6px -4px rgba(0,0,0,0.10); }}
            .transition-all {{ transition: all 0.15s ease; }}
            .transition-colors {{ transition: background-color 0.15s ease, color 0.15s ease; }}
            .focus\\:outline-none:focus {{ outline: 2px solid transparent; outline-offset: 2px; }}
            .max-h-\\[500px\\] {{ max-height: 500px; }}
            .bg-white.rounded-xl,
            .bg-white.rounded-lg {{
                background: #ffffff;
                border-radius: 0.75rem;
                border: 1px solid #f3f4f6;
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.08), 0 2px 4px -2px rgba(0,0,0,0.08);
            }}
            .tab-btn,
            .replay-btn,
            #btn-download-pdf,
            a[download] {{
                appearance: none;
                border: 0;
                cursor: pointer;
                font-family: inherit;
                text-decoration: none;
            }}
            .tab-btn {{
                background: transparent;
                color: #dbeafe;
                border-radius: 0.5rem 0.5rem 0 0;
            }}
            .tab-btn.bg-white {{
                background: #ffffff;
                color: #1e3a8a;
            }}
            .report-tabs {{
                max-width: 100%;
                overflow-x: auto;
                overscroll-behavior-x: contain;
            }}
            .replay-btn {{
                background: #f3f4f6;
                color: #4b5563;
            }}
            .replay-btn.text-white {{
                background: #1e3a8a;
                color: #ffffff;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 0.5rem 1rem;
                border-bottom: 1px solid #f3f4f6;
            }}
            thead {{
                background: #f3f4f6;
                color: #374151;
            }}
            .report-table-scroll {{
                width: 100%;
                max-width: 100%;
                overflow-x: auto;
                overscroll-behavior-x: contain;
            }}
            .metrics-table {{
                width: 100%;
                table-layout: fixed;
                color: #334155;
                font-size: 10px;
                text-align: center;
                letter-spacing: 0;
            }}
            .metrics-table th,
            .metrics-table td {{
                min-width: 0;
                padding: 0.45rem 0.125rem;
                border-right: 1px solid #e5e7eb;
            }}
            .metrics-table th:first-child,
            .metrics-table td:first-child {{
                width: 86px;
            }}
            .metrics-table thead th {{
                background: #e2e8f0;
                color: #1e293b;
                font-weight: 700;
                line-height: 1.15;
                white-space: normal;
                overflow-wrap: anywhere;
            }}
            .metrics-table tbody td {{
                white-space: nowrap;
                overflow: hidden;
            }}
            .metrics-table tbody td:first-child {{ background: #ffffff; }}
            .metrics-table tbody tr:hover td {{ background: #f8fafc; }}
            .metrics-table-fit {{
                width: 100%;
                max-width: 100%;
                overflow: hidden;
            }}
            .params-table {{ min-width: 860px; }}
            .min-h-\\[330px\\] {{ min-height: 330px; }}
            .max-h-\\[330px\\] {{ max-height: 330px; }}
            .max-h-\\[420px\\] {{ max-height: 420px; }}
            @media (min-width: 768px) {{
                .md\\:grid-cols-2 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .md\\:grid-cols-3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
                .md\\:flex-row {{ flex-direction: row; }}
                .md\\:items-center {{ align-items: center; }}
                .md\\:justify-between {{ justify-content: space-between; }}
                .md\\:self-auto {{ align-self: auto; }}
            }}
            @media (min-width: 1280px) {{
                .xl\\:grid-cols-2 {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
                .xl\\:grid-cols-3 {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
                .xl\\:grid-cols-4 {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
                .xl\\:grid-cols-5 {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
                .xl\\:col-span-2 {{ grid-column: span 2 / span 2; }}
                .xl\\:flex-row {{ flex-direction: row; }}
                .xl\\:items-end {{ align-items: flex-end; }}
                .xl\\:justify-between {{ justify-content: space-between; }}
            }}
            @media (max-width: 767px) {{
                .report-header-inner {{
                    flex-direction: column;
                    align-items: stretch;
                    gap: 0.75rem;
                }}
                .report-header-title h1 {{
                    font-size: 1.5rem;
                    line-height: 2rem;
                }}
                .report-header-actions {{
                    width: 100%;
                    align-items: stretch;
                }}
                #btn-download-pdf {{ align-self: flex-end; }}
                .report-tabs {{
                    width: 100%;
                    padding-bottom: 0.25rem;
                }}
                .report-tabs .tab-btn {{
                    flex: 0 0 auto;
                    padding-left: 1rem;
                    padding-right: 1rem;
                }}
            }}
            .tab-content {{ display: none; }}
            .tab-content.active {{ display: block; animation: fadeIn 0.3s ease-in-out; }}
            .config-frame {{ width: 100%; height: calc(100vh - 190px); min-height: 720px; border: 0; background: #ffffff; }}
            .pdf-exporting * {{ animation: none !important; transition: none !important; }}
            @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(5px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
            ::-webkit-scrollbar-track {{ background: #f1f1f1; }}
            ::-webkit-scrollbar-thumb {{ background: #c1c1c1; border-radius: 4px; }}
        </style>
        <script>
            const PDF_FILE_NAME = "{analyzer.symbol}_{analyzer.freq}_{analyzer.strategy_name}_report.pdf";

            function switchTab(tabId, btnId) {{
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(el => {{
                    el.classList.remove('bg-white', 'text-[#1e3a8a]', 'font-bold');
                    el.classList.add('text-blue-100', 'hover:bg-white/20');
                }});
                document.getElementById(tabId).classList.add('active');
                let activeBtn = document.getElementById(btnId);
                activeBtn.classList.remove('text-blue-100', 'hover:bg-white/20');
                activeBtn.classList.add('bg-white', 'text-[#1e3a8a]', 'font-bold');
                if (tabId === 'tab-config') {{
                    const configFrame = document.querySelector('#tab-config .config-frame');
                    if (configFrame && configFrame.dataset.src && configFrame.dataset.loaded !== '1') {{
                        configFrame.src = configFrame.dataset.src;
                        configFrame.dataset.loaded = '1';
                    }}
                }}
                window.dispatchEvent(new Event('resize')); 
                if (tabId === 'tab1') setTimeout(setupOverviewXAxisSync, 100);
            }}
            let overviewXAxisSyncing = false;
            function setupOverviewXAxisSync() {{
                if (!window.Plotly) return;
                const tab = document.getElementById('tab1');
                if (!tab) return;
                const graphs = Array.from(tab.querySelectorAll('.plotly-graph-div'))
                    .filter(graph => graph.layout && graph.layout.xaxis);

                graphs.forEach(graph => {{
                    if (graph.dataset.overviewXSyncBound === '1') return;
                    graph.dataset.overviewXSyncBound = '1';

                    graph.on('plotly_relayout', eventData => {{
                        if (overviewXAxisSyncing || !eventData) return;

                        const isReset = eventData['xaxis.autorange'] === true;
                        const rangeStart = eventData['xaxis.range[0]'];
                        const rangeEnd = eventData['xaxis.range[1]'];
                        if (!isReset && (rangeStart === undefined || rangeEnd === undefined)) return;

                        const sourceType = (graph.layout.xaxis && graph.layout.xaxis.type) || 'linear';
                        const update = isReset
                            ? {{ 'xaxis.autorange': true }}
                            : {{ 'xaxis.range[0]': rangeStart, 'xaxis.range[1]': rangeEnd, 'xaxis.autorange': false }};

                        overviewXAxisSyncing = true;
                        const tasks = graphs
                            .filter(other => other !== graph)
                            .filter(other => (((other.layout || {{}}).xaxis || {{}}).type || 'linear') === sourceType)
                            .map(other => Plotly.relayout(other, update).catch(() => null));

                        Promise.all(tasks).finally(() => {{
                            overviewXAxisSyncing = false;
                        }});
                    }});
                }});
            }}
            function dashboardUrlForOverview(url) {{
                if (!url) return '';
                return url.split('#')[0] + '#overview';
            }}
            function openTabFromHash() {{
                if (window.location.hash === '#config') {{
                    switchTab('tab-config', 'btn-tab-config');
                }} else if (window.location.hash === '#overview') {{
                    switchTab('tab1', 'btn-tab1');
                }}
            }}
            window.addEventListener('DOMContentLoaded', function() {{
                openTabFromHash();
                setTimeout(setupOverviewXAxisSync, 700);
            }});
            window.addEventListener('hashchange', openTabFromHash);
            window.addEventListener('message', function(event) {{
                const allowedConfigOrigins = new Set([
                    'http://localhost:8501',
                    'http://127.0.0.1:8501'
                ]);
                if (!allowedConfigOrigins.has(event.origin)) return;
                if (!event.data || event.data.type !== 'backtest-report-updated') return;
                const targetUrl = dashboardUrlForOverview(event.data.url);
                if (targetUrl) {{
                    window.location.href = targetUrl;
                }} else {{
                    window.location.hash = 'overview';
                    switchTab('tab1', 'btn-tab1');
                }}
            }});
            function switchReplay(sym) {{
                document.querySelectorAll('.replay-content').forEach(el => el.style.display = 'none');
                document.querySelectorAll('.replay-btn').forEach(el => {{
                    el.classList.remove('bg-[#1e3a8a]', 'text-white', 'shadow-md');
                    el.classList.add('bg-gray-100', 'text-gray-600', 'hover:bg-gray-200');
                }});
                document.getElementById('replay-content-' + sym).style.display = 'block';
                let btn = document.getElementById('btn-replay-' + sym);
                btn.classList.remove('bg-gray-100', 'text-gray-600', 'hover:bg-gray-200');
                btn.classList.add('bg-[#1e3a8a]', 'text-white', 'shadow-md');
                window.dispatchEvent(new Event('resize')); 
            }}

            function waitForRender(ms = 450) {{
                window.dispatchEvent(new Event('resize'));
                return new Promise(resolve => setTimeout(resolve, ms));
            }}

            function currentActiveTab() {{
                const tab = document.querySelector('.tab-content.active');
                if (!tab) return {{ tabId: 'tab1', btnId: 'btn-tab1' }};
                return {{ tabId: tab.id, btnId: 'btn-' + tab.id }};
            }}

            function cloneForPlotly(obj) {{
                if (typeof structuredClone === 'function') return structuredClone(obj);
                return JSON.parse(JSON.stringify(obj));
            }}

            async function preparePeriodReturnsForPdf() {{
                const wrapper = document.getElementById('period-returns-chart');
                if (!wrapper || !window.Plotly) return null;

                const originalGraph = wrapper.querySelector('.plotly-graph-div');
                if (!originalGraph || !originalGraph.data || originalGraph.data.length < 3) return null;

                const exportBox = document.createElement('div');
                exportBox.id = 'period-returns-pdf-export';
                exportBox.className = 'space-y-4';

                const originalDisplay = originalGraph.style.display;
                originalGraph.style.display = 'none';
                wrapper.appendChild(exportBox);

                const traceConfigs = [
                    {{ traceIndex: 0, title: '周收益 (Weekly Returns)' }},
                    {{ traceIndex: 1, title: '月收益 (Monthly Returns)' }},
                    {{ traceIndex: 2, title: '年收益 (Yearly Returns)' }}
                ];

                for (const item of traceConfigs) {{
                    const panel = document.createElement('div');
                    panel.className = 'border border-gray-100 rounded-lg p-3 bg-white';

                    const title = document.createElement('div');
                    title.className = 'text-sm font-bold text-gray-700 mb-2';
                    title.textContent = item.title;

                    const graph = document.createElement('div');
                    graph.style.width = '100%';
                    graph.style.height = '300px';

                    panel.appendChild(title);
                    panel.appendChild(graph);
                    exportBox.appendChild(panel);

                    const trace = cloneForPlotly(originalGraph.data[item.traceIndex]);
                    trace.visible = true;

                    const layout = cloneForPlotly(originalGraph.layout || {{}});
                    layout.height = 300;
                    layout.margin = {{ l: 10, r: 10, t: 20, b: 40 }};
                    layout.showlegend = false;
                    layout.updatemenus = [];
                    layout.title = null;

                    await Plotly.newPlot(graph, [trace], layout, {{
                        displayModeBar: false,
                        responsive: true
                    }});
                }}

                await waitForRender(700);
                return {{ originalGraph, originalDisplay, exportBox }};
            }}

            function restorePeriodReturnsForPdf(state) {{
                if (!state) return;
                state.exportBox.querySelectorAll('.plotly-graph-div').forEach(graph => {{
                    try {{ Plotly.purge(graph); }} catch (err) {{}}
                }});
                state.exportBox.remove();
                state.originalGraph.style.display = state.originalDisplay;
            }}

            async function preparePlotlyChartsForPdf(target) {{
                if (!target || !window.Plotly) return [];
                const states = [];
                const redrawTasks = [];

                target.querySelectorAll('.plotly-graph-div').forEach(graph => {{
                    if (!graph.data || !graph.data.length) return;
                    const hasExposureTrace = graph.data.some(trace => {{
                        const name = String((trace && trace.name) || '');
                        return name.includes('持仓名义本金') || name.includes('总持仓名义本金') || name.includes('总杠杆率');
                    }});
                    if (!hasExposureTrace) return;

                    states.push({{
                        graph,
                        traces: graph.data.map(trace => ({{
                            marker: trace.marker ? cloneForPlotly(trace.marker) : null,
                            line: trace.line ? cloneForPlotly(trace.line) : null,
                            opacity: trace.opacity
                        }}))
                    }});

                    graph.data.forEach(trace => {{
                        const name = String((trace && trace.name) || '');
                        if (name.includes('多头持仓名义本金')) {{
                            trace.marker = Object.assign({{}}, trace.marker || {{}}, {{ color: 'rgba(239, 68, 68, 0.80)' }});
                            trace.opacity = 1;
                        }} else if (name.includes('空头持仓名义本金')) {{
                            trace.marker = Object.assign({{}}, trace.marker || {{}}, {{ color: 'rgba(34, 197, 94, 0.80)' }});
                            trace.opacity = 1;
                        }} else if (name.includes('总持仓名义本金')) {{
                            trace.marker = Object.assign({{}}, trace.marker || {{}}, {{ color: 'rgba(37, 99, 235, 0.75)' }});
                            trace.opacity = 1;
                        }} else if (name.includes('总杠杆率')) {{
                            trace.line = Object.assign({{}}, trace.line || {{}}, {{ color: '#111827', width: 2.4 }});
                        }}
                    }});
                    redrawTasks.push(Promise.resolve(Plotly.redraw(graph)).catch(() => null));
                }});

                await Promise.all(redrawTasks);
                return states;
            }}

            async function restorePlotlyChartsForPdf(states) {{
                if (!states || !states.length || !window.Plotly) return;
                const redrawTasks = [];
                states.forEach(state => {{
                    state.traces.forEach((snapshot, index) => {{
                        const trace = state.graph.data[index];
                        if (!trace) return;
                        if (snapshot.marker === null) {{
                            delete trace.marker;
                        }} else {{
                            trace.marker = cloneForPlotly(snapshot.marker);
                        }}
                        if (snapshot.line === null) {{
                            delete trace.line;
                        }} else {{
                            trace.line = cloneForPlotly(snapshot.line);
                        }}
                        if (snapshot.opacity === undefined) {{
                            delete trace.opacity;
                        }} else {{
                            trace.opacity = snapshot.opacity;
                        }}
                    }});
                    redrawTasks.push(Promise.resolve(Plotly.redraw(state.graph)).catch(() => null));
                }});
                await Promise.all(redrawTasks);
            }}

            function getExportBreakpoints(target, canvas) {{
                const targetHeight = Math.max(1, target.scrollHeight || target.offsetHeight);
                const scaleY = canvas.height / targetHeight;
                const targetRect = target.getBoundingClientRect();
                const selectors = [
                    ':scope > .bg-white',
                    ':scope > .grid',
                    '.replay-content'
                ];
                const nodes = [];

                selectors.forEach(selector => {{
                    try {{
                        target.querySelectorAll(selector).forEach(el => nodes.push(el));
                    }} catch (err) {{
                        // Older browsers may not support :scope. The export still works without page hints.
                    }}
                }});

                const points = nodes
                    .map(el => {{
                        const rect = el.getBoundingClientRect();
                        return Math.round((rect.bottom - targetRect.top + target.scrollTop) * scaleY);
                    }})
                    .filter(y => y > 0 && y < canvas.height);

                points.push(canvas.height);
                return Array.from(new Set(points)).sort((a, b) => a - b);
            }}

            function chooseSliceEnd(sourceY, maxEndY, breakpoints, minSliceHeight) {{
                const minEndY = sourceY + minSliceHeight;
                const candidates = breakpoints.filter(y => y > minEndY && y <= maxEndY);
                if (candidates.length > 0) return candidates[candidates.length - 1];
                return maxEndY;
            }}

            async function addCanvasToPdf(pdf, capture, firstSection) {{
                if (!capture || !capture.canvas) return firstSection;

                const canvas = capture.canvas;
                const breakpoints = capture.breakpoints || [canvas.height];
                const margin = 6;
                const pageWidth = pdf.internal.pageSize.getWidth();
                const pageHeight = pdf.internal.pageSize.getHeight();
                const imgWidth = pageWidth - margin * 2;
                const availableHeight = pageHeight - margin * 2;
                const pageCanvasHeight = Math.max(1, Math.floor(canvas.width * availableHeight / imgWidth));
                const minSliceHeight = Math.floor(pageCanvasHeight * 0.35);

                let sourceY = 0;

                while (sourceY < canvas.height) {{
                    if (!firstSection || sourceY > 0) pdf.addPage();
                    firstSection = false;

                    const maxEndY = Math.min(canvas.height, sourceY + pageCanvasHeight);
                    const sliceEndY = chooseSliceEnd(sourceY, maxEndY, breakpoints, minSliceHeight);
                    const sliceHeight = Math.max(1, sliceEndY - sourceY);
                    const pageCanvas = document.createElement('canvas');
                    pageCanvas.width = canvas.width;
                    pageCanvas.height = sliceHeight;
                    const ctx = pageCanvas.getContext('2d');
                    ctx.fillStyle = '#f3f4f6';
                    ctx.fillRect(0, 0, pageCanvas.width, pageCanvas.height);
                    ctx.drawImage(canvas, 0, sourceY, canvas.width, sliceHeight, 0, 0, canvas.width, sliceHeight);

                    const imgHeight = sliceHeight * imgWidth / canvas.width;
                    pdf.addImage(
                        pageCanvas.toDataURL('image/jpeg', 0.9),
                        'JPEG',
                        margin,
                        margin,
                        imgWidth,
                        imgHeight
                    );

                    sourceY += sliceHeight;
                }}

                return firstSection;
            }}

            async function captureReportSection(section) {{
                switchTab(section.tabId, section.btnId);
                await waitForRender();
                const target = document.getElementById(section.targetId);
                if (!target || target.offsetHeight === 0) return null;

                const replayStates = [];
                let periodReturnsState = null;
                let plotlyPdfStates = [];
                if (section.showAllReplay) {{
                    document.querySelectorAll('.replay-content').forEach(el => {{
                        replayStates.push({{
                            el,
                            display: el.style.display,
                            marginBottom: el.style.marginBottom
                        }});
                        el.style.display = 'block';
                        el.style.marginBottom = '24px';
                    }});
                    await waitForRender(700);
                }}

                if (section.showAllPeriodReturns) {{
                    periodReturnsState = await preparePeriodReturnsForPdf();
                }}

                try {{
                    plotlyPdfStates = await preparePlotlyChartsForPdf(target);
                    if (plotlyPdfStates.length) await waitForRender(250);
                    const canvas = await html2canvas(target, {{
                        backgroundColor: '#f3f4f6',
                        scale: 2,
                        useCORS: true,
                        windowWidth: document.documentElement.scrollWidth,
                        scrollX: 0,
                        scrollY: -window.scrollY
                    }});
                    return {{
                        canvas,
                        breakpoints: getExportBreakpoints(target, canvas)
                    }};
                }} finally {{
                    await restorePlotlyChartsForPdf(plotlyPdfStates);
                    restorePeriodReturnsForPdf(periodReturnsState);
                    replayStates.forEach(state => {{
                        state.el.style.display = state.display;
                        state.el.style.marginBottom = state.marginBottom;
                    }});
                }}
            }}

            async function downloadAnalysisPdf() {{
                const btn = document.getElementById('btn-download-pdf');
                const original = currentActiveTab();
                const originalText = btn.innerText;

                if (!window.html2canvas || !window.jspdf) {{
                    alert('PDF 下载组件加载失败，请重新生成回测报告。');
                    return;
                }}

                btn.disabled = true;
                btn.innerText = '正在生成 PDF...';
                btn.classList.add('opacity-70', 'cursor-wait');
                document.body.classList.add('pdf-exporting');

                try {{
                    const {{ jsPDF }} = window.jspdf;
                    const pdf = new jsPDF('p', 'mm', 'a4');
                    const sections = [
                        {{ title: '策略总览 (Strategy Overview)', tabId: 'tab1', btnId: 'btn-tab1', targetId: 'tab1' }},
                        {{ title: '交易归因 (Trade Attribution)', tabId: 'tab2', btnId: 'btn-tab2', targetId: 'tab2', showAllPeriodReturns: true }},
                        {{ title: '信号检测 (Signal Inspection)', tabId: 'tab-signal', btnId: 'btn-tab-signal', targetId: 'tab-signal' }},
                        {{ title: '交易复盘 (Trade Replay)', tabId: 'tab3', btnId: 'btn-tab3', targetId: 'report-replay-section', showAllReplay: true }}
                    ];

                    let firstSection = true;
                    for (const section of sections) {{
                        const capture = await captureReportSection(section);
                        firstSection = await addCanvasToPdf(pdf, capture, firstSection);
                    }}

                    pdf.save(PDF_FILE_NAME);
                }} catch (err) {{
                    console.error(err);
                    alert('PDF 生成失败，请打开浏览器控制台查看错误。');
                }} finally {{
                    switchTab(original.tabId, original.btnId);
                    document.body.classList.remove('pdf-exporting');
                    btn.disabled = false;
                    btn.innerText = originalText;
                    btn.classList.remove('opacity-70', 'cursor-wait');
                }}
            }}
        </script>
    </head>
    <body class="min-h-screen">
        <div class="bg-[#1e3a8a] w-full pt-5 px-6 shadow-lg">
            <div class="report-header-inner max-w-screen-2xl mx-auto flex justify-between items-end">
                <div class="report-header-title text-white pb-4">
                    <h1 class="text-3xl font-bold tracking-wider">Backtest Report | {analyzer.symbol}</h1>
                    <p class="text-sm text-blue-200 mt-2">引擎生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')} | 策略: {analyzer.strategy_name}</p>
                </div>
                <div class="report-header-actions flex flex-col items-end gap-3">
                    <button id="btn-download-pdf" onclick="downloadAnalysisPdf()" class="bg-white/95 hover:bg-white text-[#1e3a8a] px-5 py-2 rounded-lg text-sm font-bold shadow-sm border border-white/40 transition-colors focus:outline-none">
                        下载PDF报告
                    </button>
                    <div class="report-tabs flex space-x-1">
                        <button id="btn-tab-config" onclick="switchTab('tab-config', 'btn-tab-config')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">配置中心 (Configuration)</button>
                        <button id="btn-tab1" onclick="switchTab('tab1', 'btn-tab1')" class="tab-btn bg-white text-[#1e3a8a] font-bold px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none shadow-[0_-2px_10px_rgba(0,0,0,0.1)]">策略总览 (Strategy Overview)</button>
                        <button id="btn-tab2" onclick="switchTab('tab2', 'btn-tab2')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">交易归因 (Trade Attribution)</button>
                        <button id="btn-tab-signal" onclick="switchTab('tab-signal', 'btn-tab-signal')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">信号检测 (Signal Inspection)</button>
                        <button id="btn-tab3" onclick="switchTab('tab3', 'btn-tab3')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">复盘明细 (Replay & Logs)</button>
                    </div>
                </div>
            </div>
        </div>

        <div class="max-w-screen-2xl mx-auto p-6">

            <div id="tab-config" class="tab-content">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <iframe class="config-frame" src="about:blank" data-src="http://localhost:8501/?embed=1" title="Backtest Configuration"></iframe>
                </div>
            </div>

            <div id="tab1" class="tab-content active space-y-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-4">回测配置 (Backtest Settings)</h2>
                    <div class="w-full">{html_params}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-4">绩效指标 (Performance Metrics)</h2>
                    <div class="w-full">{html_metrics}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-2">动态权益曲线 (Equity Curve)</h2>
                    <div class="w-full">{html_fig_eq}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-red-500 pl-3 mb-2">累计盈亏与交易成本 (Cumulative PnL & Costs)</h2>
                    <div class="w-full">{html_fig_cum}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#8b5cf6] pl-3 mb-2">净值与基准对比 (Net Value vs Benchmark)</h2>
                    <div class="w-full">{html_fig_nv_bench}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-red-600 pl-3 mb-2">历史滚动回撤 (Rolling Drawdown)</h2>
                    <div class="w-full">{html_fig_dd}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-slate-900 pl-3 mb-2">持仓敞口与杠杆率 (Exposure & Leverage)</h2>
                    <div class="w-full">{html_fig_leverage}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-orange-500 pl-3 mb-2">保证金占用率 (Margin Utilization)</h2>
                    <p class="text-xs text-gray-500 pl-3 mb-2">实际冻结保证金 ÷ 动态权益；通常与杠杆率同向，但会按持仓品种的加权保证金率缩放。</p>
                    <div class="w-full">{html_fig_margin_utilization}</div>
                </div>
            </div> 

            <div id="tab2" class="tab-content space-y-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-2">多品种净盈亏贡献 (Asset PnL Contribution)</h2>
                    <div class="w-full">{html_fig_pnl_bar}</div>
                </div>
                <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                    <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-blue-600 pl-3 mb-4">持仓周期分布 (Holding Period Distribution)</h2>
                        <div class="w-full flex justify-center">{html_fig_holding_pie}</div>
                    </div>
                    <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-emerald-500 pl-3 mb-2">品种交易市值与收益 (Trade Notional & PnL)</h2>
                        <p class="text-xs text-gray-400 mb-2 pl-3">面积为实际成交市值，颜色为品种净收益；红色为盈利，绿色为亏损，颜色越深绝对值越大。</p>
                        <div class="w-full flex justify-center">{html_fig_turnover_pie}</div>
                    </div>
                </div> 
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-purple-600 pl-3 mb-2">多品种累计盈亏曲线 (Asset PnL Curves)</h2>
                    <p class="text-xs text-gray-400 mb-2 pl-3">默认显示全部品种曲线；可切换为板块合计。图例按板块分组，点击单个品种隐藏或显示该品种。</p>
                    <div class="w-full">{html_fig_pnl_curves}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-orange-500 pl-3 mb-2">逐笔盈亏分布 (Trade PnL Distribution)</h2>
                    <div class="w-full">{html_fig_pnl_dist}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-teal-500 pl-3 mb-2">多周期收益 (Period Returns)</h2>
                    <div id="period-returns-chart" class="w-full">{html_fig_period_ret}</div>
                </div>
            </div> 

            <div id="tab-signal" class="tab-content space-y-6">
                {html_signal_diagnostics}
            </div>

            <div id="tab3" class="tab-content space-y-6">
                {html_replay_section}

                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100 bg-[#1e3a8a]/5">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3">交易流水明细 (Trade Log)</h2>
                    </div>
                    {html_trades}
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100 bg-teal-50">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-teal-500 pl-3">资金流水明细 (Fund Flow)</h2>
                    </div>
                    {html_funds}
                </div>
            </div> 

        </div> 
    </body>
    </html>
    """

    out_path = os.path.abspath(os.path.join(analyzer.output_dir, '0_Dashboard_Interactive.html'))
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_template)

    print("[Engine Front] 看板生成完成。")
    if open_browser:
        if start_config_ui:
            _ensure_streamlit_config_ui()
        webbrowser.open(f"file://{out_path}")
    return out_path

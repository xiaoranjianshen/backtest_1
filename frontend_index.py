# -*- coding: utf-8 -*-
import os
import json
import socket
import subprocess
import sys
import time
import webbrowser
import pandas as pd


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
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/jspdf@2.5.1/dist/jspdf.umd.min.js"></script>
        <style>
            body {{ background-color: #f3f4f6; }}
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
                    alert('PDF 下载组件加载失败，请确认网络可访问 jsDelivr CDN 后刷新页面。');
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
            <div class="max-w-screen-2xl mx-auto flex justify-between items-end">
                <div class="text-white pb-4">
                    <h1 class="text-3xl font-bold tracking-wider">Backtest Report | {analyzer.symbol}</h1>
                    <p class="text-sm text-blue-200 mt-2">引擎生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')} | 策略: {analyzer.strategy_name}</p>
                </div>
                <div class="flex flex-col items-end gap-3">
                    <button id="btn-download-pdf" onclick="downloadAnalysisPdf()" class="bg-white/95 hover:bg-white text-[#1e3a8a] px-5 py-2 rounded-lg text-sm font-bold shadow-sm border border-white/40 transition-colors focus:outline-none">
                        下载PDF报告
                    </button>
                    <div class="flex space-x-1">
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
                    <iframe class="config-frame" src="http://localhost:8501/?embed=1" title="Backtest Configuration"></iframe>
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

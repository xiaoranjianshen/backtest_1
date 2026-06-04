# -*- coding: utf-8 -*-
import os
import webbrowser
import pandas as pd


def build_html_dashboard(analyzer):
    if analyzer is None:
        print("❌ [前端工厂] 未接收到有效数据 (可能无交易记录产生)，终止渲染前端看板。")
        return
    print("🎨 [前端工厂] 正在组装全屏垂直瀑布流看板...")

    # 1. 从 analyzer 提取基础图表 HTML 代码块
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

    # 2. 提取并组装【交互式复盘中心】(Tab 3 顶部)
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
        <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
            <h2 class="text-lg font-bold text-gray-800 border-l-4 border-indigo-600 pl-3 mb-4">交互式买卖复盘 (Trade Replay Center)</h2>
            <div class="flex space-x-3 overflow-x-auto pb-3 mb-2 border-b border-gray-100">
                {buttons_html}
            </div>
            <div class="w-full relative mt-2">
                {divs_html}
            </div>
        </div>
        """

    # 3. 提取交易流水表 (Trades Log)
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

    # 4. 提取资金流表 (Fund Flow)
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

    # 5. 组装前端 UI 骨架
    html_template = f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>{analyzer.strategy_name} - Backtest</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
        <style>
            body {{ background-color: #f3f4f6; }}
            .tab-content {{ display: none; }}
            .tab-content.active {{ display: block; animation: fadeIn 0.3s ease-in-out; }}
            @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(5px); }} to {{ opacity: 1; transform: translateY(0); }} }}
            ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
            ::-webkit-scrollbar-track {{ background: #f1f1f1; }}
            ::-webkit-scrollbar-thumb {{ background: #c1c1c1; border-radius: 4px; }}
        </style>
        <script>
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
            }}
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
        </script>
    </head>
    <body class="min-h-screen">
        <div class="bg-[#1e3a8a] w-full pt-5 px-6 shadow-lg">
            <div class="max-w-screen-2xl mx-auto flex justify-between items-end">
                <div class="text-white pb-4">
                    <h1 class="text-3xl font-bold tracking-wider">Backtest | {analyzer.symbol} 可视化终端</h1>
                    <p class="text-sm text-blue-200 mt-2">引擎生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')} | 策略: {analyzer.strategy_name}</p>
                </div>
                <div class="flex space-x-1">
                    <button id="btn-tab1" onclick="switchTab('tab1', 'btn-tab1')" class="tab-btn bg-white text-[#1e3a8a] font-bold px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none shadow-[0_-2px_10px_rgba(0,0,0,0.1)]">产品业绩 (Overview)</button>
                    <button id="btn-tab2" onclick="switchTab('tab2', 'btn-tab2')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">交易分析 (Attribution)</button>
                    <button id="btn-tab3" onclick="switchTab('tab3', 'btn-tab3')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">复盘明细 (Replay & Logs)</button>
                </div>
            </div>
        </div>

        <div class="max-w-screen-2xl mx-auto p-6">

            <div id="tab1" class="tab-content active space-y-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-4">回测配置</h2>
                    <div class="w-full">{html_params}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-1">
                    <div class="w-full">{html_metrics}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-2">动态资金曲线 (Interactive)</h2>
                    <div class="w-full">{html_fig_eq}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-red-500 pl-3 mb-2">累计盈亏与摩擦</h2>
                    <div class="w-full">{html_fig_cum}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#8b5cf6] pl-3 mb-2">净值表现与基准对比</h2>
                    <div class="w-full">{html_fig_nv_bench}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-red-600 pl-3 mb-2">历史滚动回撤 (Rolling Drawdown)</h2>
                    <div class="w-full">{html_fig_dd}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-slate-900 pl-3 mb-2">组合隔夜持仓敞口与杠杆率双轴监控 (Portfolio Leverage)</h2>
                    <div class="w-full">{html_fig_leverage}</div>
                </div>
            </div> 

            <div id="tab2" class="tab-content space-y-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-2">多品种横截面净盈亏排序 (Alpha Contribution)</h2>
                    <div class="w-full">{html_fig_pnl_bar}</div>
                </div>
                <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                    <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-blue-600 pl-3 mb-4">资产持仓周期透视 (Holding Cycles)</h2>
                        <div class="w-full flex justify-center">{html_fig_holding_pie}</div>
                    </div>
                    <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-emerald-500 pl-3 mb-4">品种成交额占比 (Turnover Weight)</h2>
                        <div class="w-full flex justify-center">{html_fig_turnover_pie}</div>
                    </div>
                </div> 
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-purple-600 pl-3 mb-2">多品种盈亏曲线簇 (Asset PnL Cluster)</h2>
                    <p class="text-xs text-gray-400 mb-2 pl-3">💡 提示：点击图例可以动态隐藏/显示特定品种曲线。</p>
                    <div class="w-full">{html_fig_pnl_curves}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-orange-500 pl-3 mb-2">逐笔极值盈亏分位数 (Quantile Distribution)</h2>
                    <div class="w-full">{html_fig_pnl_dist}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-teal-500 pl-3 mb-2">多周期收益日历 (Period Returns)</h2>
                    <div class="w-full">{html_fig_period_ret}</div>
                </div>
            </div> 

            <div id="tab3" class="tab-content space-y-6">
                {html_replay_section}

                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100 bg-[#1e3a8a]/5">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3">交易流水明细 (Trades Log)</h2>
                    </div>
                    {html_trades}
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100 bg-teal-50">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-teal-500 pl-3">历史资金流表 (Fund Flow)</h2>
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

    print(f"🚀 [前端工厂] 网页生成完毕！正在唤醒浏览器...")
    webbrowser.open(f"file://{out_path}")
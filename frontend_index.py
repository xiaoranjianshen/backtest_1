# -*- coding: utf-8 -*-
import os
import webbrowser
import pandas as pd


def build_html_dashboard(analyzer):
    print("🎨 [前端工厂] 正在组装全屏垂直瀑布流看板...")

    # 从 analyzer 获取 HTML 代码块
    html_metrics = analyzer.get_metrics_table_html()
    html_params = analyzer.get_params_table_html()
    html_fig_eq = analyzer.get_equity_html_div()
    html_fig_cum = analyzer.get_cum_pnl_html_div()

    # 提取交易流水表
    if hasattr(analyzer, 'match_df') and not analyzer.match_df.empty:
        df_t = analyzer.match_df[
            ['open_time', 'close_time', 'symbol', 'direction', 'volume', 'open_price', 'close_price', 'net_pnl',
             'commission']].copy()
        df_t.columns = ['开仓时间', '平仓时间', '合约', '方向', '手数', '开仓价', '平仓价', '净盈亏', '手续费']
        df_t['净盈亏'] = df_t['净盈亏'].apply(
            lambda x: f"<span class='{'text-red-500' if x > 0 else 'text-green-500'} font-bold'>{x:.2f}</span>")
        html_trades = df_t.to_html(index=False, border=0, escape=False,
                                   classes="w-full text-sm text-center text-gray-600 bg-white")
        html_trades = html_trades.replace('<thead>', '<thead class="bg-gray-100 text-gray-700 sticky top-0 shadow-sm">') \
            .replace('<th>', '<th class="py-3 px-4">') \
            .replace('<td>', '<td class="py-2 px-4 border-b border-gray-50">')
    else:
        html_trades = "<p class='p-4 text-gray-500'>无交易流水</p>"

    # 组装深蓝色 Tailwind 模板 (严格独占一行的垂直布局)
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
            ::-webkit-scrollbar-thumb:hover {{ background: #a8a8a8; }}
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
        </script>
    </head>
    <body class="min-h-screen">
        <div class="bg-[#1e3a8a] w-full pt-5 px-6 shadow-lg">
            <div class="max-w-screen-2xl mx-auto flex justify-between items-end">
                <div class="text-white pb-4">
                    <h1 class="text-3xl font-bold tracking-wider">Backtest | {analyzer.symbol} 组合可视化</h1>
                    <p class="text-sm text-blue-200 mt-2">引擎生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')} | 策略: {analyzer.strategy_name}</p>
                </div>
                <div class="flex space-x-1">
                    <button id="btn-tab1" onclick="switchTab('tab1', 'btn-tab1')" class="tab-btn bg-white text-[#1e3a8a] font-bold px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none shadow-[0_-2px_10px_rgba(0,0,0,0.1)]">产品业绩 (Overview)</button>
                    <button id="btn-tab2" onclick="switchTab('tab2', 'btn-tab2')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">交易分析 (Attribution)</button>
                    <button id="btn-tab3" onclick="switchTab('tab3', 'btn-tab3')" class="tab-btn text-blue-100 hover:bg-white/20 px-8 py-3 rounded-t-lg text-sm transition-all focus:outline-none">流水明细 (Logs)</button>
                </div>
            </div>
        </div>

        <div class="max-w-screen-2xl mx-auto p-6">

            <div id="tab1" class="tab-content active space-y-6">

                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-4">回测配置</h2>
                    <div class="overflow-x-auto w-full">
                        {html_params}
                    </div>
                </div>

                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-1 overflow-hidden">
                    <div class="overflow-x-auto max-h-[350px] w-full">
                        {html_metrics}
                    </div>
                </div>

                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-[#1e3a8a] pl-3 mb-2">动态资金曲线 (Interactive)</h2>
                    <div class="w-full">
                        {html_fig_eq}
                    </div>
                </div>

                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-red-500 pl-3 mb-2">累计盈亏与摩擦</h2>
                    <div class="w-full">
                        {html_fig_cum}
                    </div>
                </div>

            </div>

            <div id="tab2" class="tab-content">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-10 flex flex-col items-center justify-center min-h-[500px]">
                    <h3 class="text-xl font-bold text-gray-700">多维归因模块</h3>
                    <p class="text-gray-500 mt-2">（即将接入...）</p>
                </div>
            </div>

            <div id="tab3" class="tab-content">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-1">
                    <div class="overflow-y-auto max-h-[750px]">
                        {html_trades}
                    </div>
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
# Backtest Agent Integration

The Streamlit backtest platform now includes a dedicated `Agent 助手` tab.
This tab connects to the separate `backtest_agent` Django service and exposes
the agent inside the original backtest platform.

## What The Tab Does

- Ask general questions about what the system can do.
- Preview an agent plan before executing any tool.
- Execute low-risk read-only tools directly.
- Execute medium-risk tools only after explicit confirmation.
- Open or embed the full Backtest Agent Workbench.

## Recommended Startup

```powershell
cd C:\Users\tnori\PyCharmMiscProject\backtest_1
C:\Users\tnori\AppData\Local\Programs\Python\Python311\python.exe ui\start_ui.py
```

`ui/start_ui.py` first checks whether Backtest Agent is already listening at:

```text
http://127.0.0.1:8010
```

If it is not running, the script attempts to start:

```text
C:\Users\tnori\PyCharmMiscProject\backtest_agent
```

Then it starts the original Streamlit backtest platform at:

```text
http://127.0.0.1:8501
```

To disable automatic agent startup:

```powershell
$env:BACKTEST_AGENT_AUTOSTART = "0"
```

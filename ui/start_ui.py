# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


BACKTEST_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKTEST_ROOT.parent
AGENT_ROOT = WORKSPACE_ROOT / "backtest_agent"
AGENT_HOST = os.getenv("BACKTEST_AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.getenv("BACKTEST_AGENT_PORT", "8010"))
RUNNER_PID_PATH = AGENT_ROOT / "var" / "runjobs.pid"
RUNNER_INTERVAL_SECONDS = float(os.getenv("BACKTEST_AGENT_RUNNER_INTERVAL", "2.0"))


def _port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _agent_python() -> Path:
    if os.name == "nt":
        candidate = AGENT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = AGENT_ROOT / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else Path(sys.executable)


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        except OSError:
            return False
        output = result.stdout.strip()
        return str(pid) in output and "INFO:" not in output.upper()

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _runner_is_running() -> bool:
    try:
        pid = int(RUNNER_PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return _process_is_running(pid)


def _start_agent_if_available() -> None:
    if os.getenv("BACKTEST_AGENT_AUTOSTART", "1").strip().lower() in {"0", "false", "no"}:
        return
    if _port_is_open(AGENT_HOST, AGENT_PORT):
        return
    manage_py = AGENT_ROOT / "manage.py"
    if not manage_py.exists():
        return

    log_dir = AGENT_ROOT / "var"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "runserver-from-backtest-platform.out.log"
    stderr_path = log_dir / "runserver-from-backtest-platform.err.log"
    stdout = open(stdout_path, "a", encoding="utf-8", errors="replace")
    stderr = open(stderr_path, "a", encoding="utf-8", errors="replace")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [
                str(_agent_python()),
                str(manage_py),
                "runserver",
                f"{AGENT_HOST}:{AGENT_PORT}",
            ],
            cwd=str(AGENT_ROOT),
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    finally:
        stdout.close()
        stderr.close()

    for _ in range(20):
        if _port_is_open(AGENT_HOST, AGENT_PORT):
            return
        time.sleep(0.5)


def _start_runner_if_available() -> None:
    if os.getenv("BACKTEST_AGENT_RUNNER_AUTOSTART", "1").strip().lower() in {"0", "false", "no"}:
        return
    manage_py = AGENT_ROOT / "manage.py"
    if not manage_py.exists() or _runner_is_running():
        return

    log_dir = AGENT_ROOT / "var"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "runjobs-from-backtest-platform.out.log"
    stderr_path = log_dir / "runjobs-from-backtest-platform.err.log"
    stdout = open(stdout_path, "a", encoding="utf-8", errors="replace")
    stderr = open(stderr_path, "a", encoding="utf-8", errors="replace")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = os.environ.copy()
    env.setdefault("BACKTEST_AGENT_SETTINGS", "backtest_agent.config.settings.local")
    try:
        process = subprocess.Popen(
            [
                str(_agent_python()),
                str(manage_py),
                "runjobs",
                "--loop",
                "--interval",
                str(RUNNER_INTERVAL_SECONDS),
                "--limit",
                "1",
            ],
            cwd=str(AGENT_ROOT),
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            env=env,
        )
        RUNNER_PID_PATH.write_text(str(process.pid), encoding="utf-8")
    finally:
        stdout.close()
        stderr.close()


def main():
    _start_agent_if_available()
    _start_runner_if_available()
    app_path = Path(__file__).resolve().parent / "app.py"
    subprocess.run([
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        "8501",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ], check=False)


if __name__ == "__main__":
    main()

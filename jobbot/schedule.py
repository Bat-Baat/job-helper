"""Turns the daily Autopilot on or off using the computer's own scheduler.

Windows: Task Scheduler. Mac: launchd. Linux: cron.
The computer has to be on and signed in at that time. On Mac and Windows a missed run
(e.g. the laptop was asleep) happens as soon as it wakes up.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from . import store

ROOT = Path(__file__).resolve().parent.parent
NAME = "JobHelperAutopilot"
SYSTEM = platform.system()


def _hm(t: str) -> tuple[int, int]:
    h, m = t.split(":")
    return int(h), int(m)


def install(time_str: str) -> str:
    h, m = _hm(time_str)
    py = sys.executable
    if SYSTEM == "Windows":
        bat = store.DATA_DIR / "run_autopilot.bat"
        bat.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\n"{py}" -m jobbot.autopilot >> "{store.DATA_DIR / "autopilot_output.txt"}" 2>&1\r\n')
        xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><CalendarTrigger><StartBoundary>2026-01-01T{h:02d}:{m:02d}:00</StartBoundary>
    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Settings><StartWhenAvailable>true</StartWhenAvailable><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT14H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings>
  <Actions><Exec><Command>{escape(str(bat))}</Command></Exec></Actions>
</Task>"""
        xml_file = store.DATA_DIR / "autopilot_task.xml"
        xml_file.write_text(xml, encoding="utf-16")
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", NAME, "/XML", str(xml_file)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr or r.stdout)
        return f"Scheduled every day at {time_str} (Windows Task Scheduler)."
    if SYSTEM == "Darwin":
        plist = Path.home() / "Library/LaunchAgents/com.jobhelper.autopilot.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jobhelper.autopilot</string>
  <key>ProgramArguments</key><array><string>{escape(py)}</string><string>-m</string><string>jobbot.autopilot</string></array>
  <key>WorkingDirectory</key><string>{escape(str(ROOT))}</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>
  <key>StandardOutPath</key><string>{escape(str(store.DATA_DIR / "autopilot_output.txt"))}</string>
  <key>StandardErrorPath</key><string>{escape(str(store.DATA_DIR / "autopilot_output.txt"))}</string>
</dict></plist>""")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        r = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr or r.stdout)
        return f"Scheduled every day at {time_str} (macOS)."
    # Linux / other: cron
    line = (f'{m} {h} * * * cd "{ROOT}" && DISPLAY=${{DISPLAY:-:0}} "{py}" -m jobbot.autopilot '
            f'>> "{store.DATA_DIR / "autopilot_output.txt"}" 2>&1 # {NAME}')
    _set_cron(line)
    return f"Scheduled every day at {time_str} (cron)."


def _set_cron(line: str | None) -> None:
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    lines = [l for l in cur.splitlines() if NAME not in l] + ([line] if line else [])
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def uninstall() -> str:
    if SYSTEM == "Windows":
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", NAME], capture_output=True)
    elif SYSTEM == "Darwin":
        plist = Path.home() / "Library/LaunchAgents/com.jobhelper.autopilot.plist"
        subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True)
        plist.unlink(missing_ok=True)
    else:
        try:
            _set_cron(None)
        except (OSError, subprocess.CalledProcessError):
            pass
    return "Autopilot schedule turned off."


def is_installed() -> bool:
    if SYSTEM == "Windows":
        return subprocess.run(["schtasks", "/Query", "/TN", NAME], capture_output=True).returncode == 0
    if SYSTEM == "Darwin":
        return (Path.home() / "Library/LaunchAgents/com.jobhelper.autopilot.plist").exists()
    try:
        return NAME in subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except OSError:
        return False

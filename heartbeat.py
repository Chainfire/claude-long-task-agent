#!/usr/bin/env python3.12
"""Heartbeat timer for long-running subagents: sleep, then remind to re-arm.

Run in the background (Bash run_in_background). Its completion wakes the agent,
whose next turn keeps the prompt cache warm.
"""
import sys
import time

minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 45
time.sleep(minutes * 60)
arg = "" if minutes == 45 else f" {sys.argv[1]}"
print(f"HEARTBEAT ({minutes:g} min). Check on your background work. If you are still "
      "waiting on anything, re-arm the heartbeat now: Bash with run_in_background and "
      f'command `python3.12 "$HOME/.claude/hooks/heartbeat.py"{arg}`. '
      "If you are finishing or handing off, don't re-arm.")

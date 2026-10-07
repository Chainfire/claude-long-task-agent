#!/usr/bin/env python3.12
"""PostToolUse hook: tell a subagent to hand off once its context grows large.

Warns at --first tokens, again every --every tokens after that, and on every
tool call once --max is reached. Per-agent state, including those three
thresholds, lives in STATE_DIR/<agent_id>.json; the hook's command line only
seeds it. Any failure exits 2 with a message on stderr, which Claude Code shows
to the agent, so a broken monitor never goes unnoticed.

To retune a running agent (takes effect on its next tool call):
    python3.12 ~/.claude/hooks/context_watch.py --set <agent_id> [--first N] [--every N] [--max N]
Only the given values change; step and misses are reset.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".cache" / "claude-context-watch"
CHUNK = 256 * 1024
MAX_MISSES = 3
STALE_AFTER = 2 * 24 * 3600  # seconds
HARD_LIMIT = 850_000  # higher thresholds are capped to this, with a warning
DEFAULTS = {"first": 175_000, "every": 25_000, "max": 700_000}

SAFE_POINT = "no half-edited files, no orphaned processes, nothing left mid-change"
CLEANUP = ("Before you write it, clean up: stop every heartbeat, monitor and background shell you started "
           "(TaskStop), so nothing can wake you after you exit. Jobs that must keep running must be detached "
           "from your own shells (not tied to a Bash run_in_background launcher); list them in the handoff "
           "with names, PIDs and log paths. Once you have exited, never edit the handoff again: send any "
           "later correction to your managing agent instead.")
FRESH_AGENT = "It must let a fresh agent continue your work without access to your transcript."
REPORT = ("Tell your managing agent the full path of that document and that you are "
          "exiting, and exit.")


def tokens(s):
    s = s.strip().upper()
    mult = {"K": 1_000, "M": 1_000_000}.get(s[-1:], 0)
    n = int(float(s[:-1]) * mult) if mult else int(s)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"must be positive: {s}")
    return n


def apply_cap(data):
    """Normalize the thresholds in data to ints, capping them at HARD_LIMIT
    and first at max.

    Returns one warning per capped value.
    """
    warnings = []
    for k in DEFAULTS:
        n = tokens(str(data[k]))
        if n > HARD_LIMIT:
            warnings.append(f"{k}={n} exceeds the hard limit; capped to {HARD_LIMIT}.")
            n = HARD_LIMIT
        data[k] = n
    if data["first"] > data["max"]:
        warnings.append(f"first={data['first']} exceeds max; capped to {data['max']}.")
        data["first"] = data["max"]
    return warnings


def last_context(path):
    """Context size of the newest assistant turn, reading the file backwards."""
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        buf = b""
        while end > 0:
            start = max(0, end - CHUNK)
            f.seek(start)
            buf = f.read(end - start) + buf
            end = start
            lines = buf.split(b"\n")
            if start > 0:
                buf, lines = lines[0], lines[1:]  # first line may be partial
            for line in reversed(lines):
                if b'"usage"' not in line or b'"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:  # last line still being appended
                    continue
                u = (rec.get("message") or {}).get("usage") if rec.get("type") == "assistant" else None
                if not u:
                    continue
                n = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                     + u.get("cache_read_input_tokens", 0))
                if n:  # skip synthetic zero-usage messages
                    return n
    return None  # normal on an agent's first tool call


def load(path):
    """The agent's state dict, or None if it has no state file yet."""
    try:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:  # maybe caught a non-atomic edit mid-write
            time.sleep(0.1)
            data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data


def save(path, data):
    """Atomically write a state file, then delete state files untouched for STALE_AFTER."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    cutoff = time.time() - STALE_AFTER
    for f in STATE_DIR.iterdir():
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except FileNotFoundError:  # another agent's hook removed it first
            pass


def handoff_path(cwd, session_id, key):
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    base = (Path(r.stdout.strip()) / ".claude" / "handoffs" if r.returncode == 0
            else Path.home() / ".claude" / "handoffs" / session_id)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{key}.md"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=tokens)
    ap.add_argument("--every", type=tokens)
    ap.add_argument("--max", type=tokens)
    ap.add_argument("--set", metavar="AGENT_ID",
                    help="update that agent's state with the given values instead of running as a hook")
    a = ap.parse_args()
    given = {k: v for k in DEFAULTS if (v := getattr(a, k)) is not None}

    if a.set:
        state = STATE_DIR / f"{a.set}.json"
        data = load(state)
        if data is None:
            print(f"note: no state for {a.set} yet; creating it", file=sys.stderr)
            data = dict(DEFAULTS)
        data |= given | {"step": -1, "misses": 0}
        for w in apply_cap(data):
            print(f"warning: {w}", file=sys.stderr)
        save(state, data)
        print(json.dumps(data, indent=2))
        return

    hook = json.load(sys.stdin)
    session_id = hook["session_id"]
    agent_id = hook.get("agent_id")
    if agent_id:
        transcript = (Path(hook["transcript_path"]).parent / session_id / "subagents"
                      / f"agent-{agent_id}.jsonl")
        key = agent_id
    else:  # main thread (--agent); not the intended use, but keep working
        transcript, key = Path(hook["transcript_path"]), session_id

    # The state file wins over the command line, so --set can retune a running
    # agent. Unknown keys are kept as they are.
    state = STATE_DIR / f"{key}.json"
    data = load(state) or {}
    defaults = DEFAULTS | given | {"step": -1, "misses": 0}
    before = dict(data)
    data = defaults | data
    capped = apply_cap(data)
    dirty = data != before
    first, every, limit = data["first"], data["every"], data["max"]
    notes = [f"CONTEXT MONITOR WARNING: {w}" for w in capped]

    ctx = last_context(transcript)
    if ctx is None:
        data["misses"] = int(data["misses"]) + 1
        if data["misses"] >= MAX_MISSES:
            raise RuntimeError(f"no assistant usage found in {transcript} "
                               f"({data['misses']} tool calls in a row)")
        save(state, data)
        return emit(notes)
    if data["misses"]:
        data["misses"], dirty = 0, True

    last = int(data["step"])
    if ctx < first:
        step = -1
    elif ctx >= limit:
        step = None
    else:
        step = (ctx - first) // every

    if step is not None and step != last:  # new step, or lower after compaction
        data["step"], dirty = step, True
    if dirty:
        save(state, data)
    if step is not None and step <= last:
        return emit(notes)

    path = handoff_path(hook.get("cwd") or os.getcwd(), session_id, key)
    k = f"{ctx // 1000}K"
    if step is None:
        msg = (f"NOTICE — CONTEXT LIMIT REACHED: Your context is now {k} tokens. Hand off now. "
               "Start no new work; finish only the step you are in until you reach a safe point "
               f"({SAFE_POINT}). {CLEANUP} Then write "
               f"the handoff document to {path}. {FRESH_AGENT} {REPORT}")
    else:
        msg = (f"NOTICE: Your context is now {k} tokens. Unless handing off would cause "
               f"problems, at the next safe point ({SAFE_POINT}) write a handoff document to "
               f"{path}. {FRESH_AGENT} {CLEANUP} {REPORT}")
    emit(notes + [msg])


def emit(notes):
    """Pass messages to the agent as extra context after this tool call."""
    if notes:
        json.dump({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                          "additionalContext": "\n\n".join(notes)}}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        if isinstance(e, SystemExit) and e.code in (0, None):
            raise
        if "--set" in sys.argv:  # a controller ran it by hand; keep the plain error
            raise
        print(f"CONTEXT MONITOR FAILURE (~/.claude/hooks/context_watch.py): "
              f"{type(e).__name__}: {e}. Your context size is NOT being monitored. "
              "Report this failure to your managing agent.", file=sys.stderr)
        sys.exit(2)

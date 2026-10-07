# claude-long-task-agent

## ABOUT

A Claude Code sub-agent built specifically for long-running tasks that Claude monitors. Designed for Claude subscriptions; the cost/benefit differs for API usage. Uses cache keep-alive heartbeats, context-size monitoring, and automatic hand-off.

On my personal projects, where Claude can be running for days, running dozens of sub-agents which run and monitor commands that can each take hours to complete, this reduces subscription consumption speed by around 60%. It does not reduce the number of tokens itself (in fact it slightly increases them).

There are three parts to this sub-agent:

- **Sub-agent cache TTL is set to 1 hour.** The default is 5 minutes, which means that any time a sub-agent takes more than 5 minutes between turns, its cache expires and the entire context of the sub-agent is written to the cache again, rather than a much cheaper cache-read of the existing context and a cache-write only for the new tokens. However, 1-hour cache writes are more expensive than 5-minute ones (2× versus 1.25× the base input price), so this might not fit your usage pattern: sub-agents that never pause for more than 5 minutes pay the higher write price for no benefit. This is a global setting (`CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h`), it applies to all sub-agents, not just this one!

- **Heartbeat.** The sub-agent is instructed to run anything that may take longer than 5 minutes in the background so it doesn't block the agent, and to keep a heartbeat armed while it waits: `heartbeat.py` runs in the background, sleeps 45 minutes, and then wakes the sub-agent. That turn re-reads the context, which keeps the cache warm. The heartbeat's output reminds the sub-agent to re-arm it each time it fires. This uses additional tokens, but keeping the cache warm usually more than makes up for it.

- **Context monitor.** Through a `PostToolUse` hook, `context_watch.py` runs after every tool call and reads the sub-agent's current context size from its transcript. At 175K tokens the sub-agent is told to write a hand-off document at the next safe point, so a fresh agent can take over its work. This message is repeated every 25K tokens, and from 700K tokens on it changes, on every tool call, to tell the sub-agent it must hand off immediately. The parent agent can adjust these limits for a running sub-agent (the command is in the agent's description in `long-task-sub.md`). Once the sub-agent reports the path of its hand-off document, the parent stops it and starts a fresh `long-task-sub` with "Continue the work described in <path>".

Hand-off documents are written to `<repo>/.claude/handoffs/<agentId>.md` when working inside a git repository (you may want to add that directory to `.gitignore`), or to `~/.claude/handoffs/<sessionId>/<agentId>.md` otherwise. Per-agent monitor state is kept in `~/.cache/claude-context-watch/` and cleaned up 2 days after the agent's last tool call.

**YOUR MILEAGE MAY VARY**. It might drain your subscription faster for your specific use-case. Do not blindly assume my experience fits yours. **USE AT YOUR OWN RISK**.

Only tested on Linux, with Python 3.12.

## WHY 175K TOKENS

This number was picked from analysis of my own prompts and contexts. Every turn re-reads the sub-agent's entire context from the cache, so the larger the context, the more each turn costs, and a long-lived sub-agent ends up spending most of its tokens re-reading old context. On the other hand, a fresh agent reading the hand-off from the previous agent and re-exploring what it needs costs cache-writes. For my workload the cost-optimal hand-off point is somewhere around 100–135K tokens (the more a fresh agent needs to re-explore, the higher), with a fairly flat cost curve around it. That is too small for my agents to work with, and 175K still captures most of the savings.

## INSTRUCTIONS FOR THE USER

Tell Claude Code:

```
Clone the repo at https://github.com/Chainfire/claude-long-task-agent to a temporary directory, and follow the instructions from the "INSTRUCTIONS FOR CLAUDE" section
```

Note that Claude generally won't pick this sub-agent on its own: it prefers the general-purpose agent. You need to tell it to use `long-task-sub`, either in your session, or permanently through `CLAUDE.md` or Claude's memory. For example:

```
Use the long-task-sub agent instead of the general-purpose agent for any sub-agent task that may run longer than a few minutes.
```

## INSTRUCTIONS FOR CLAUDE

- Check whether `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` is set to `1h` in the user's Claude Code configuration (usually the `env` block of `~/.claude/settings.json`) or environment. If not, offer to set it, explaining how it may affect costs (see ABOUT). Abort the installation if the user declines. If you do change it to `1h`, the user must restart the Claude Code session before continuing.

- Check the included Python scripts `heartbeat.py` and `context_watch.py` for compatibility with the user's system and setup. They were written for Python 3.12 and work on 3.9 and up (not on 3.8 or older). Both refer to the `python3.12` binary: `heartbeat.py` in the re-arm command it prints, `context_watch.py` in its shebang and usage text. Adjust these to the user's Python if needed. If everything checks out, install both into `~/.claude/hooks/`, or into the repository's `.claude/hooks/` for a per-repo installation.

- Check the agent definition `long-task-sub.md` (frontmatter and prompt) for compatibility with the user's system and setup. In particular you might need to adjust the Python binary and the script locations, which appear in three places: the hook command in the frontmatter, the `--set` command in the `description`, and the heartbeat command in the prompt. For a per-repo installation, point these at the repository's `.claude/hooks/` instead of `$HOME/.claude/hooks/`. If everything checks out, install it into `~/.claude/agents/`, or into the repository's `.claude/agents/` for a per-repo installation. The user then needs to restart the Claude Code session, or run the `/reload-plugins` command, for the agent to become available.

- Offer the user a test run to make sure everything works as expected. Keep it cheap: run the agent on Sonnet (its default; don't use Haiku, which doesn't follow the agent's instructions reliably enough) with greatly reduced limits so the notices fire quickly (for example `--first 40K --every 10K --max 80K`, applied with `--set` once the agent is running), and a short heartbeat (`heartbeat.py 1` sleeps 1 minute). Check that the heartbeat wakes the agent, that the hand-off notices arrive, and that the hand-off document is written to the expected path.

## LICENSE

MIT, see [LICENSE](LICENSE).

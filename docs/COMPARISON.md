# Coordina and related projects

Developers who run several AI agents at once already have good options for *parts* of the problem. This page explains what each one is built for, where Coordina fits, and where others are clearly ahead. Descriptions come from each project's README/metadata as read on 2026-11-14; nothing here was benchmarked.

## Where Coordina fits

Coordina is built for one job: **hand a task to a conversation that is already open in another vendor's desktop app, and get a recoverable, correlated reply** — with delivery, response and execution tracked separately and no automatic resend when delivery is uncertain. It is macOS-only, stdlib-only and has no daemon.

If you want something else — a big multi-model hub, a visual board, terminal session management, or the official Claude→Codex path — one of the projects below is probably a better fit.

## Projects by category

### Claude Code ↔ Codex bridges
- **[AgentBridge](https://github.com/raysonmeng/agent-bridge)** (MIT) — local bidirectional collaboration between Claude Code and Codex. The most popular live bridge between those two tools.
- **[claude-codex-mcp-bridge](https://github.com/WebisityStudio/claude-codex-mcp-bridge)** (MIT) — MCP bridge over the desktop transports. **Coordina's `live.py` transports were developed with reference to it** (see [NOTICE](../NOTICE)).
- **[claude-uds-bridge](https://github.com/LeonKohli/claude-uds-bridge)** (MIT) — messaging between Codex tasks and Claude Code sessions over Unix sockets.

*Difference:* these connect one vendor pair, live. Coordina adds Antigravity (Gemini), Grok Build and DeepSeek Harness as requesters, a durable job record, and explicit uncertain-delivery handling.

### Official delegation
- **[codex-plugin-cc](https://github.com/openai/codex-plugin-cc)** (Apache-2.0, by OpenAI) — use Codex from Claude Code to review code or delegate tasks. Official, polished, one direction.

*Difference:* Coordina is multi-directional and targets already-open chats; the official plugin is the safer choice if Claude→Codex is all you need.

### CLIs and models as MCP tools / second opinions
- **[PAL MCP Server](https://github.com/BeehiveInnovations/pal-mcp-server)** (formerly Zen MCP) — one MCP server orchestrating many providers and CLIs (Gemini, OpenAI, OpenRouter, Azure, …). Far broader model coverage and community.
- **[ai-cli-mcp](https://github.com/mkXultra/ai-cli-mcp)** (MIT) — run Claude, Codex and Gemini CLI agents in the background from any MCP client.
- **[agent-mux](https://github.com/buildoak/agent-mux)** (MIT) — cross-engine dispatch for Codex, Claude Code and Gemini CLI with one JSON contract.
- **[Deliberation](https://github.com/antonbabenko/deliberation)** (MIT) — second opinions from Codex, Gemini, Grok and hundreds of OpenRouter models.
- **[Ask LLM](https://github.com/Lykhoyda/ask-llm)** (MIT) — MCP + CLI so Claude Code, Codex and Cursor can consult other models.
- **[cc-multi-cli-plugin](https://github.com/greenpolo/cc-multi-cli-plugin)** (Apache-2.0) — use Codex, Cursor, OpenCode Zen and Antigravity models inside Claude Code.

*Difference:* these mostly start a new process or API call per request. Coordina's fresh-executor route (`--route new`) overlaps with them; its distinct part is delivery into open chats. PAL and Deliberation package multi-model consensus/debate, which Coordina does not.

### Coordination, mailboxes and dashboards
- **[MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail_rust)** — Rust MCP server for multi-agent coordination with a Git-backed archive and many tools.
- **[vibe-kanban](https://github.com/BloopAI/vibe-kanban)** (Apache-2.0) — board/orchestration UI for coding agents.
- **[claude-squad](https://github.com/smtg-ai/claude-squad)** (AGPL-3.0) — manage multiple terminal agents (Claude Code, Codex, OpenCode, Amp).

*Difference:* different layer. Coordina has no UI and a minimal store-and-poll peer mailbox; these are stronger for visual management and rich messaging.

## Coordina's focus vs. where others are stronger

| Coordina focuses on | Others are stronger at |
|---|---|
| Delivering into **already-open** desktop chats (Codex, Claude Code, Antigravity), keeping their history and permissions | Cross-platform support (Linux/Windows) |
| Requesters beyond Claude Code: Codex, Antigravity, Grok Build, DeepSeek Harness | Number of models/providers (PAL, Deliberation) |
| Durable SQLite job record; reply recovered by `job_id` after reconnects | Visual dashboards (vibe-kanban, claude-squad) |
| Delivery / response / execution tracked separately; no automatic resend of uncertain deliveries; idempotent `request_id` | Rich agent messaging and archives (MCP Agent Mail) |
| macOS memory-pressure admission and RSS watchdog | Official support and stability (codex-plugin-cc) |
| Zero dependencies, no daemon, no port | English-first docs, install polish, community size |

## Community signals

Threads showing demand for agents that talk to each other — today usually answered with a two-vendor socket bridge:

- r/mcp — [claude-uds-bridge: an MCP server that connects Codex…](https://www.reddit.com/r/mcp/comments/1wmo8o1/claudeudsbridge_an_mcp_server_that_connects_codex/)
- r/ClaudeCode — [My Claude Code and Codex sessions can now talk to…](https://www.reddit.com/r/ClaudeCode/comments/1w9q6dm/my_claude_code_and_codex_sessions_can_now_talk_to/)
- r/ClaudeCode — [Claude Code chatting with Codex in two separate…](https://www.reddit.com/r/ClaudeCode/comments/1tmrnc3/claude_code_chatting_with_codex_in_two_separate/)

## Limits of this page

No alternative was installed or tested. Discord and Product Hunt were not surveyed; Reddit comment sentiment was not analyzed. Corrections from maintainers are welcome via PR.

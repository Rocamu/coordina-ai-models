# Coordina — presentation

## One-liner
**Coordinate work *between* AI models — hand a task to the Claude, Codex or Gemini chat you already have open, and get the reply back by job ID, even after you reconnect.**

Spanish: *Coordina el trabajo entre modelos de IA: pásale una tarea al chat de Claude, Codex o Gemini que ya tienes abierto, y recupera la respuesta por ID aunque te reconectes.*

Repository: `coordina-ai-models` — the name says it: Coordina coordinates *between* models, not agents inside a single model.

## Milestone
As of November 2026, Coordina has been tested coordinating **9 models**: Codex, Claude, Gemini (via Antigravity), Grok, DeepSeek, GLM, Nemotron, Qwen and Meta AI (through a local app bridge, disabled in this release) — with completed jobs recorded for each. Any model reachable from the same environment (a laptop, a server, a swarm of machines) through a desktop app, a CLI or an API can be added as a target.

## Who it is for
Developers and researchers on a Mac who keep **two or more AI apps open at once** (Codex desktop, Claude Code, Antigravity) or who mix commercial and open-source models, and want them to work together without copy-paste — and without losing each chat's context.

## The problem
You already have a Codex chat that knows your repo and a Claude session that knows your plan. Getting one to ask the other means copy-paste, or a bridge that spins up a fresh, context-less process. And when something goes wrong you can't tell: did the message arrive? Was it answered? Will a retry run it twice?

## The answer (three proofs)
1. **It lands in the chat you already have open** — same history, same model, same permissions.
2. **The reply survives you** — durable SQLite job; recover it by `job_id` after closing or reconnecting the requester.
3. **It tells you what it doesn't know** — delivery, response and execution tracked separately; an uncertain delivery is reported as `delivery_unknown` and never resent automatically.

Supporting facts: stdlib-only Python, no daemon, no port, macOS memory guard, 202 tests, MIT.

## What has been coordinated
- A three-model review of this repository (Claude, Codex, Gemini via Antigravity) — [DEBATE.md](DEBATE.md).
- Cross-vendor second opinions between open Claude Code and Codex chats.
- Bulk text jobs on DeepSeek, GLM and Nemotron.
- Qwen — in that case an open-source model running on a remote GPU.
- Fresh Codex, Claude Code and Grok executors when no open chat fits.

## What we do NOT promise
Exactly-once delivery · verified execution (`completed` = a reply arrived) · OS-level isolation · stability of undocumented app interfaces · cross-platform support.

## 45-second demo script
| Time | Screen | Caption |
|---|---|---|
| 0–8 s | Claude Code: `/coordina with Codex: review this diff and name the riskiest change` | "Ask the Codex chat you already have open" |
| 8–15 s | Coordina proposes open Codex chats for this folder; pick one | "It finds your open chats — you choose" |
| 15–25 s | Codex desktop: the task appears in that chat and Codex answers | "Same chat, same context" |
| 25–33 s | Quit the requester mid-wait; reopen; `coordina wait <id>` → reply with `COORDINA[id]` | "Close everything. The reply is still there." |
| 33–42 s | A job shows `delivery_unknown`; no resend happens | "When it isn't sure, it says so — and doesn't resend" |
| 42–45 s | Repo URL + `python3 install.py` | "macOS · stdlib only · MIT" |

The README embeds the demo as `docs/demo.gif`, cut from the launch video that follows this script.

## Positioning vs. alternatives
- *"Isn't this AgentBridge / claude-uds-bridge?"* — Those connect Claude Code and Codex live. Coordina coordinates across more models, keeps a durable job record and handles uncertain delivery explicitly.
- *"Why not the official Codex plugin?"* — If Claude→Codex is all you need, use it. Coordina is multi-directional and targets chats you already have open.
- *"Why not PAL / Deliberation?"* — They win on model breadth via APIs and consensus. Coordina is about delivery into existing conversations and a recoverable job record.
- *"Isn't reading Antigravity's token sketchy?"* — It reads the token Antigravity passes to its own process, same user, only to talk to `127.0.0.1`; the method is documented in DEBATE.md and SECURITY.md, and EDR tools may flag it.

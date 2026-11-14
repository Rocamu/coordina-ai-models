# Pre-launch review: three models, one tool

We're sharing this document because it is itself a product of what Coordina does. Before deciding whether this repository was ready for GitHub, we used Coordina to coordinate three models — Claude, Codex and Gemini (via Antigravity) — and asked them to review the package, how to position it and how to present it. They disagreed on several points; we kept those disagreements visible instead of smoothing them over.

*Les compartimos este documento porque es, justamente, producto de lo que hace Coordina: antes de aprobar este repo para GitHub, usamos la herramienta para coordinar a tres modelos —Claude, Codex y Gemini (vía Antigravity)— y pedirles una revisión del paquete, de cómo plantearlo y de cómo presentarlo. No siempre estuvieron de acuerdo, y dejamos esos desacuerdos a la vista.*

Date: 2026-11-14. All requests ran in **consult mode only**: no model edited files (`git status` was clean after every round).

## How the review ran

| Round | Model | Coordina route | Outcome |
|---|---|---|---|
| R1-Claude | Claude | `existing` — an existing Claude Code session (one earlier attempt failed on CLI authentication and was retried this way) | completed |
| R1-Codex | Codex | `new` — fresh read-only executor | completed |
| R1-Gemini | Gemini (Antigravity) | `new` — new visible Antigravity chat | `response_pending`: Antigravity asked for a UI approval, which Coordina does not grant by design |
| R2-Claude | Claude | `existing` — same session | completed |
| R2-Codex | Codex | `new` — fresh executor, given the round-1 summary | completed |
| R1+R2-Gemini | Gemini (Antigravity) | `new` — material pasted into the prompt, no tools | completed |

Round 2 gave each model a summary of the others' critiques and asked for a yes/no position on each disputed point. Gemini reviewed the README, the comparison and code facts pasted into its prompt, not the whole repository. The pending R1-Gemini job is itself a good example of Coordina's separated states: delivered, no reply, not retried.

## Consensus (all three)

1. **Owner-specific optional extensions out of the default path** — move them to an `extras/` area, disabled by default and enabled explicitly, never by mere file presence.
2. **Keep the DeepSeek official API route** (its budget ledger is legitimate and distinctive).
3. **Package as `coordina/`** — top-level modules such as `install`, `live`, `routing` would collide in users' environments.
4. **Lead with the open-chat flow** (Codex, Claude Code, Antigravity); move other targets to an "Also: headless executors" section.
5. **Demo = proof, not logos:** delegate to an open chat → see the task arrive → reconnect the requester → recover by `job_wait` → show `delivery_unknown` not being resent.
6. **The comparison was defensive**; drop star counts; describe what each project does.
7. **Ship as a "public preview"** with explicit limits. *(The maintainer chose to go public from day one, without a private beta; problems are reported as GitHub issues.)*
8. **English MCP tool descriptions are needed** (the calling model reads them).

## Disagreements

| Topic | Claude | Codex | Gemini |
|---|---|---|---|
| Free routes (OpenCode free models, FreeLLM/NVIDIA) — **resolved by the maintainer: keep as optional adapters** | Move to extras | Keep as extras with requirements and provider terms documented | Remove |
| Translate before launch | MCP descriptions yes; skill/errors later | English happy path (install, first job, recovery, permissions, `delivery_unknown`) + its MCP descriptions/errors | Yes: schemas and errors |
| Version | `v0.9.0` preview | Keep `2.3.14` + "public preview" label | `v0.9.0` |
| Formatting pass | Before the first public commit | Only touched areas; not a blocker | (package restructure yes) |
| Per-project capability matrix in the comparison | Against (costly, sounds accusatory) | For | — |
| "Never sent twice" in the hook | Proposed it | Against: avoiding *automatic* resends doesn't prove no duplicates | Used "never resends on uncertain delivery" |
| Credit wording for the MIT ancestor | "Portions derived from" if code was translated | Clarify what comes from the ancestor; ship notices with the runtime and plugin ZIP | Full notice + mark derived routines in `live.py` |

## Other notable points
- **Codex:** CI should exercise the installer; `LICENSE`/`NOTICE` should ship with the installed runtime and plugin ZIP; README and SECURITY must agree on how credentials are handled; document data retention; explain how to resolve `delivery_unknown`.
- **Claude:** add `--dry-run` and `--uninstall` to the installer; prefer each CLI's own `mcp add` command over editing config files; API keys via environment variables with file fallback; publish a tested-versions table per transport.
- **Gemini:** the Antigravity integration depends on internal methods and local process inspection (see below), which can break with app updates and can be flagged by endpoint-security tools; frame the comparison around "ephemeral sockets fail when you close the laptop".
- Factual correction to Gemini: the full upstream MIT text is already reproduced in `THIRD_PARTY_NOTICES.md` and referenced from `NOTICE`.

## How Coordina reaches Antigravity (replicable method)

Antigravity does not expose a public API for talking to an open chat, so Coordina uses the same local channel the app uses for its own sidecars:

1. **Find the language server.** List the current user's processes (`/bin/ps -axww -o pid= -o uid= -o lstart= -o command=`) and keep those whose executable is `…/Antigravity*.app/Contents/Resources/bin/language_server`, owned by the current user, regular file, not group/world-writable.
2. **Read its CSRF token.** Antigravity starts that process with `--csrf_token <value>` on its command line; Coordina reads the value from the process arguments. It never reads other users' processes or files.
3. **Find its loopback port.** `lsof -nP -a -p <pid> -iTCP -sTCP:LISTEN` lists the ports; only `127.0.0.1` / `::1` are accepted. The plain (non-TLS) port speaks Connect JSON and gRPC.
4. **Call it locally.** Requests go to `http://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/<Method>` with header `x-codeium-csrf-token: <token>`, with proxies disabled so the token never leaves the machine. `GetAllCascadeTrajectories` lists open chats; `GetCascadeTrajectorySteps` reads replies.
5. **Deliver.** Coordina runs the app's own agent CLI, `language_server agentapi send-message …` (or `new-conversation`), with `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN` in the environment. The message arrives as an agent message, not as user input.

See `antigravity_servers()`, `agy_rpc()` and `agentapi()` in `live.py`, and `docs/native-protocols.md`. Caveats: these are undocumented internals verified with Antigravity 2.24.0; endpoint-security (EDR) tools may flag reading another process's arguments, even your own app's.

## Hooks proposed
- Claude: "Hand a task to the Claude, Codex or Gemini chat you already have open — and always know whether it arrived, got answered, or is unknown. Survives reconnects; never sent twice."
- Codex: "Hand off a task to another AI agent in an already-open conversation, then retrieve its reply—even after you reconnect."
- Gemini: "Delegate work to your open Claude, Codex, or Gemini desktop chats — with a durable SQLite queue that never resends on uncertain delivery."
- **Adopted:** "Hand a task to the Claude, Codex or Gemini chat you already have open — and get the reply back by job ID, even after you reconnect."

## What changed after the review
- README (EN/ES) reordered: hook → demo → quick start → first result → states table → tested versions → integrations → headless executors (optional adapters demoted) → CLI → limits → architecture → comparison.
- "Honest states" wording replaced by "delivery, response and execution tracked separately"; no exactly-once claim.
- Credentials statement clarified (CLI logins vs. API key files); data-retention and Antigravity-token notes added.
- Comparison rewritten by category, without star counts, with a "where others are stronger" column.

## Findings resolved after the review
Two problems raised in the review were fixed before the public release (see `CHANGELOG.md`):
- **Tool auto-approval in fresh executors** (raised by Codex and Claude). Grok executors ran with `--always-approve`, a local forum MCP was injected pre-approved, and the installer pre-approved Coordina's own MCP tools in Codex. All of this is now **off by default** and enabled only with an explicit opt-in (`install.py --auto-approve` or `COORDINA_AUTO_APPROVE=1`); without it, Grok executor jobs are refused with a clear message.
- **Installer could stop half-way** (raised by Codex). It read `~/.claude.json` unconditionally and had no rollback. It now checks prerequisites before writing anything, stages the runtime, keeps a journal of every file it creates or modifies, rolls everything back on failure or interruption, and is safe to re-run.

## Try it yourself
Want your own agent to set Coordina up? See **[Install with an AI agent](../README.md#install-with-an-ai-agent)** in the README for a ready-to-paste prompt for Codex, Claude Code or Grok.

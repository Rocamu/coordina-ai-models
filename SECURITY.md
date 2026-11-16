# Security policy

## Supported versions
Only the latest release receives fixes.

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private vulnerability reporting ("Report a vulnerability" in the Security tab): https://github.com/Rocamu/coordina-ai-models/security/advisories/new
We aim to acknowledge within 7 days.

## Threat model (summary)
- Coordina runs locally as the current user. It opens **no network port**; MCP uses stdio.
- Messages delivered between agents are **untrusted input** for the receiver; they never carry user consent and never widen the receiver's permissions. Coordina does not approve UI permission prompts.
- To reach Antigravity's local language server, Coordina reads the CSRF token Antigravity passes to its own process, from the process list (`/bin/ps`, same user, app-bundle executable only), and talks only to `127.0.0.1`. Endpoint-security (EDR) tools may flag this pattern.
- CLI targets use each CLI's own login. API adapters (DeepSeek official, FreeLLM) read keys from files you create under `~/.config/agent-ecosystem/secrets/` (keep `0600`). Coordina never copies keys into its state.
- `consult`/`edit` map to each CLI's sandbox/permission flags. They are **not** OS-level isolation.
- **Tool auto-approval is off by default.** Fresh Codex executors run sandboxed (`read-only` / `workspace-write`) without approval escalation; fresh Claude Code executors run with an explicit tool allow-list (everything else denied); Grok executors are refused unless you opt in; the installer does not pre-approve Coordina's MCP tools in Codex. Opt in explicitly with `python3 install.py --auto-approve` (persists `"auto_approve": true` in `config.json`) or `COORDINA_AUTO_APPROVE=1` per process (`0` forces it off). With the opt-in, Grok runs with `--always-approve` inside its `read-only` / `coordina-grok` sandbox profile and Codex pre-approves Coordina's MCP tools — only enable it if you accept that executors act without per-call confirmation.
- **Installer:** checks prerequisites (macOS, Python ≥ 3.9, `codex`/`claude` on PATH, write permissions, valid client JSON) before writing anything; uses no network; stages the runtime before replacing it; and rolls back every file it created or modified if a step fails or the run is interrupted (`SIGINT`/`SIGTERM`). Re-running it is safe.
- State (`~/.local/share/coordina`) is created with `0700`/`0600` and keeps task text, replies and transcripts until you delete them (no automatic pruning yet). Treat it as sensitive.

## Out of scope
Breakage caused by vendor app updates to undocumented interfaces (please report as a regular bug).

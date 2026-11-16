# Changelog

All notable changes to this project are documented here. Versions before the
first public release are summarized from the private history.

## [Unreleased]

## [2.3.14] - 2026-11-15
- **Security: tool auto-approval is now opt-in.** Grok executors (`--always-approve`) are refused unless `COORDINA_AUTO_APPROVE=1` or `"auto_approve": true` (`install.py --auto-approve`); the local forum MCP is no longer injected or pre-approved by default; the installer no longer writes `default_tools_approval_mode="approve"` for Coordina in Codex unless opted in (an existing opt-in is preserved on reinstall).
- **Installer is transactional and idempotent:** preflight checks (macOS, Python ≥ 3.9, `codex`/`claude`, write permissions, valid client JSON) before any write; staged runtime deployment; journal-based rollback on any failure or SIGINT/SIGTERM; a missing `~/.claude.json` no longer crashes the install; re-running skips an already-registered Codex server.
- Tests for both (`tests/test_safety_defaults.py`), 220 tests in total (after later additions).
- README: 9-model milestone (historical, Meta AI route disabled in this release), Gemini counted via Antigravity, public Roadmap / Known limitations (bring-your-own Qwen planned), install-with-an-AI-agent prompt, English primary with a Spanish translation.
- Prepared for public release: English/Spanish README, comparison, license, notices, CI, templates.
- README/COMPARISON rewritten after a Claude/Codex/Gemini review (docs/DEBATE.md).
- Personal paths replaced by configuration: `COORDINA_OUTPUT_ROOT` for `output_path` (OpenCode routes), placeholder default for the self-hosted Qwen client (`joker.cli`).
- Current runtime. Per-job `model` / `reasoning_effort` for codex, claude and grok (route new, one round); FreeLLM NVIDIA allow-list; DeepSeek official budget ledger.

## [2.3.12]
- Two concurrent slots for independent executors; memory controls and path claims preserved; shared DeepSeek budget.

## [2.3.0]
- Requester origin becomes optional (local or cloud sessions); replies bound to `job_id`.
- `target=cowork` UI route with `ui_receipt` (URL and marker checks).
- Fixed 2.2 regression that required `source_required`.

## [2.2]
- Routing by session identity; persistent UI receipts.

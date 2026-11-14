# Contributing

Thanks for your interest! Coordina is small on purpose: stdlib only, no daemon, honest states.

## Ground rules
- **No third-party runtime dependencies.** Python 3.9+ standard library only.
- **Never weaken delivery semantics.** Uncertain delivery must stay `delivery_unknown` and must not be retried automatically; a busy/offline target must not be silently replaced by a new session.
- **Tests first.** Every behavior change needs a test in `tests/`. Tests must isolate state with a temporary `COORDINA_HOME` and must not touch the real home directory.
- **No personal data or secrets** in code, tests, fixtures or docs. Use placeholders like `/path/to/project`.
- Undocumented app interfaces (Codex desktop IPC, Antigravity language server) must fail closed on unknown shapes.

## Workflow
1. Fork and create a branch.
2. `python3 -m unittest discover -s tests -v`
3. Open a PR using the template; describe user-visible changes in `CHANGELOG.md`.

## Language
User-facing strings are currently Spanish. English translations / i18n PRs are welcome; keep both in sync.

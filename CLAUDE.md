# Frappe Connect

Native two-way sync hub for Frappe: push DocType changes out to SaaS tools
(Google Sheets, Slack, ...), pull external changes back in. Config-driven —
adding a connector is a new file, never a DocType/event-engine edit.

Full PRD/HLD/LLD: `docs/PRD_HLD_LLD.md` — this file is architecture-as-built +
gotchas, not a duplicate of that spec.

## Bench context

- Bench root: `~/The_Base/frappe-bench-2`, Frappe 15.104.0, bench 5.25.9
- Site: `mysite.localhost`, `developer_mode: 1`
- This app: `apps/frappe_connect` — own git repo (`bench new-app` auto-inits),
  branch `main`, pushed to https://github.com/ayushhCreator/frappe_connect
- Reference-only clone (read, don't copy code): `../../reference/data_migration_tool`
  — the prior one-shot migration tool this hub evolved from. Its defects
  (undefined role breaking `bench migrate`, non-functional `api_methods` hook
  key, 1000+ line files, zero tests, `setup.py` needing a missing
  `requirements.txt`) are the anti-patterns this app must not repeat.

## Module layout — the one gotcha that bit twice

`bench new-app` creates a nested structure. Get this wrong and imports fail
with `ModuleNotFoundError` at `bench migrate`:

```text
apps/frappe_connect/
  pyproject.toml
  frappe_connect/                      <- pip package root ("frappe_connect")
    hooks.py
    connectors/                        <- package ROOT level, sibling of hooks.py
      base.py exceptions.py retry.py google_sheets.py
      tests/
    event_engine/                      <- package ROOT level, sibling of hooks.py
      registry.py dispatcher.py webhooks.py
      tests/
    frappe_connect/                    <- MODULE subfolder (module = "Frappe Connect")
      doctype/                         <- doctypes nest one level DEEPER than connectors/event_engine
        connector_configuration/
        connector_field_map/
        sync_log/
        connector_sync_map/
```

`connectors/` and `event_engine/` import as `frappe_connect.connectors` /
`frappe_connect.event_engine` — package root, NOT
`frappe_connect.frappe_connect.connectors`. Only `doctype/` lives inside the
extra `frappe_connect/frappe_connect/frappe_connect/` module folder.

## Status

**Done (M1 + M2), all verified — migrate clean, 23/23 tests pass, ruff clean:**

- DocTypes: Connector Configuration, Connector Field Map (child), Sync Log,
  Connector Sync Map — System Manager only, no undefined role
- `connectors/base.py` — `Connector` ABC (`push`/`pull`) + `SyncResult`
  (Success/Partial Failure/Failed status logic)
- `connectors/exceptions.py` — `TransientConnectorError` (retryable) vs
  `PermanentConnectorError` (fails fast, never retried)
- `connectors/retry.py` — `@with_retry()`, 3x exponential backoff, only
  wraps `TransientConnectorError`. Regression-tested against the exact bug
  HLD describes: a permanent config error must not get retried 3x.
- `connectors/google_sheets.py` — service-account JSON key auth (not
  `frappe.integrations.google_oauth`/`Google Settings` — that's a shared
  site-wide OAuth connection, wrong fit for per-config independent
  credentials). `credentials` field = the raw service-account JSON key.
  `config` field = `{"spreadsheet_id": ..., "sheet_name": ...}`.
- `event_engine/registry.py` — `@register("Type Name")` self-registration +
  `pkgutil` auto-discovery of everything under `connectors/`. Adding
  connector #N is dropping a file — zero edits here.
- `event_engine/dispatcher.py` — `on_doc_change` (doc_events `"*"` hook,
  filters to matching enabled configs, enqueues `push_one`), `push_one`,
  `run_scheduled_pulls`/`pull_one`, shared `upsert_record` (used by both
  scheduled pull and webhook — same upsert path either way)
- `event_engine/webhooks.py` — `receive` (`allow_guest=True`, POST-only,
  HMAC-SHA256 checked before anything is enqueued, rejects with
  `frappe.PermissionError` on bad signature)
- `hooks.py` wired: `doc_events["*"]`, `scheduler_events.cron` every 15 min
- `dispatcher.retry_sync()` + Sync Log "Retry Job" button — DLQ-style manual
  retry for failed/partial syncs, re-enqueues `push_one`/`pull_one_by_name`
- `registry.get_connector_types()` (whitelisted) + Connector Configuration's
  `connector_type.js` `onload` — populates the Select's options from the live
  registry, so adding a connector still never touches the DocType JSON
- `dispatcher._cast_value()` — external pull/webhook payloads (all strings)
  get cast to the target field's real type before upsert
- `connectors/rate_limit.py`'s `throttle()` — fixed-window limiter backed by
  `frappe.cache()` (shared across RQ workers), called before every
  `connector.push()`/`pull()`, keyed per Connector Configuration name

**Known gaps / not yet done:**

- M3: Slack connector — not started
- M4: real end-to-end run against a live Google Sheet — only mocked so far
- M5: packaging/demo/Marketplace submission — not started

## Architecture notes / footguns

- `dispatcher.push_one(configuration_name, docname)` takes **strings** (it's a
  `frappe.enqueue` target — args must be serializable). `dispatcher.pull_one(configuration)`
  takes an **already-loaded doc**. Easy to swap by mistake — check the caller.
- `webhooks.receive` imports `_map_external_to_frappe`, `_write_sync_log`, and
  `upsert_record` directly from `dispatcher.py` (two of those are
  underscore-private). Webhook and scheduled-pull intentionally share this one
  upsert path — don't fork it, but know that changing those signatures breaks
  webhooks silently (no import-time check).
- `Connector Configuration.config` is a Code/JSON string field — callers
  `json.loads` it. `credentials` / `webhook_secret` are Password fields; read
  only via `get_decrypted_password(..., raise_exception=False)`, never
  `doc.credentials` directly.
- `Connector Sync Map` has no unique constraint on
  `(connector_configuration, external_id)` — dedup relies entirely on the
  `frappe.db.get_value` lookup inside `upsert_record`. Concurrent pulls/webhooks
  for the same external record could double-insert.
- `Sync Log` is insert-only (`in_create: 1`, sorted by `creation` not
  `modified`); `status` is always derived from `SyncResult.status`, never set
  directly by callers.
- `Connector Configuration.connector_type` (Select, options unpopulated —
  see Known gaps) must match a `registry.py` `@register("...")` key by
  convention only; nothing validates the two stay in sync.

## Testing conventions

Split is by *what's under test*, not by directory (`event_engine/tests/`
contains both kinds):

- Anything touching Frappe (DB, doc events) → `frappe.tests.utils.FrappeTestCase`
  with real DB inserts and manual `tearDown` (doctype `test_*.py`,
  `event_engine/tests/test_dispatcher.py`).
- Pure logic → plain `unittest.TestCase`, no DB/Frappe context
  (`connectors/tests/test_base.py`, `test_registry.py`, `test_retry.py`,
  `test_google_sheets.py`).
- Google Sheets mocking recipe: `@patch(".google_sheets.build")` +
  `@patch(".google_sheets.Credentials")`, drive
  `service.spreadsheets.return_value.values.return_value.{get,append}.return_value.execute`.
  Patch `connectors.retry.time.sleep` in retry tests to keep them fast.
- `test_dispatcher.py` registers a throwaway `@register("Test Echo")`
  connector so dispatcher logic can be tested without a real SaaS call.

## Commands

```bash
bench --site mysite.localhost migrate
bench --site mysite.localhost run-tests --app frappe_connect
bench --site mysite.localhost run-tests --app frappe_connect --module frappe_connect.connectors.tests.test_retry
ruff check frappe_connect/frappe_connect   # run from apps/frappe_connect/
ruff format frappe_connect/frappe_connect
```

`allow_tests` is already enabled on `mysite.localhost` (`set-config allow_tests true`).

## Rules carried from the HLD review

- No `hooks.py` key that isn't a real Frappe hook — verify against installed
  frappe source before using one (the old repo's fake `api_methods` key is
  the cautionary example)
- Whitelist via `@frappe.whitelist()` on the function itself, never a
  hooks.py list
- Every DocType permission role must exist (stock role or shipped fixture)
  before `bench migrate` — an undefined role broke the old repo's migrate
- Split before ~300-400 lines per file
- No `except Exception: pass`; retry scope stays explicit per failure type
- Never log `credentials`/`webhook_secret` values, even inside Sync Log
  `errors`
- Every DocType and every non-trivial module ships a test

## Available skills (global, `~/.agents/skills/`)

Loaded on-demand by name/description, not always in context — invoke via
Skill tool when the task matches:

- `frappe-app-dev` — full-stack Frappe: doctypes, controllers/hooks,
  whitelisted APIs, desk/portal UI, background jobs, tests, `bench` commands.
  Default pick for most work in this repo.
- `app-development` — app scaffolding, hooks.py patterns, service layers,
  cross-cutting concerns (caching/logging/error handling).
- `doctype-development` — schema design, child tables, controller lifecycle
  (`validate`/`on_update`/etc).
- `api-development` — REST/RPC endpoints, `@frappe.whitelist()`, auth/perms.
- `testing` — `FrappeTestCase` vs plain `unittest`, fixtures, CI patterns.
- `code-style` — general readability/structure rules; defer to
  `frappe-app-dev` for anything Frappe-specific.
- `quality-code-review` — Frappe/ERPNext-flavored review checklist
  (correctness, security, perf, concurrency, API design). Use for reviewing
  a diff/PR — run from a **fresh session**, not the implementing one.

## Agentic workflow rules (this project)

Distilled from the Claude Code / agentic-engineering course — apply, don't
re-read the source:

- New independent task/feature → fresh session. Don't pile unrelated work
  into one context window (context rot).
- For non-trivial changes: plan mode first, implement in small vertical
  slices that each work end-to-end (e.g. one connector, one doctype field,
  one dispatcher path at a time) — not one giant patch.
- Reviewer ≠ implementer. Review a diff from a fresh session (or
  `quality-code-review` skill), never self-review in the same context that
  wrote the code.
- Before implementing new Frappe patterns, check how core Frappe/ERPNext
  does it first — imitate established conventions over inventing new ones.
- Keep PRs small and reviewable; commit after each working phase, not at
  the end of a whole milestone.
- If this file and `docs/PRD_HLD_LLD.md` drift apart during implementation,
  reconcile the spec before committing — don't let them silently diverge.
- Keep this file curated, not exhaustive — only stable, always-true project
  facts belong here; anything task-specific belongs in a skill or a one-off
  prompt.

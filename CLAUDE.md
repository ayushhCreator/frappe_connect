# Frappe Connect

Native two-way sync hub for Frappe: push DocType changes out to SaaS tools
(Google Sheets, Slack, ...), pull external changes back in. Config-driven —
adding a connector is a new file, never a DocType/event-engine edit.

Full PRD/HLD/LLD lives in the owning conversation history and `reference/`
notes (not duplicated here) — this file is architecture-as-built + gotchas.

## Bench context

- Bench root: `~/The_Base/frappe-bench-2`, Frappe 15.104.0, bench 5.25.9
- Site: `mysite.localhost`, `developer_mode: 1`
- This app: `apps/frappe_connect` — own git repo (`bench new-app` auto-inits),
  branch `develop`, no remote yet
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

**Known gaps / not yet done:**

- `connector_type` field is a bare Select with no options populated —
  needs a small doctype `.js` to fetch registry keys and set
  `frm.set_df_property('connector_type', 'options', [...])` dynamically
  (so adding a connector still never touches the DocType JSON)
- M3: Slack connector — not started
- M4: real end-to-end run against a live Google Sheet — only mocked so far
- M5: packaging/demo/Marketplace submission — not started
- No GitHub remote yet, nothing pushed

## Commands

```bash
bench --site mysite.localhost migrate
bench --site mysite.localhost run-tests --app frappe_connect
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

# Frappe Connect — PRD, HLD & LLD

**Status:** Draft v1
**Owner:** Ayush
**Related:** evolved from the `data_migration_tool` portfolio review

---

## 1. Problem & Context

Frappe/ERPNext businesses currently connect their site to other SaaS tools
(Zoho, WhatsApp, Google Sheets, Slack, Shopify) using either paid iPaaS
tools (Zapier, self-hosted n8n) or one-off custom scripts nobody
maintains. There is no clean, open-source, native integration layer for
Frappe — unlike Salesforce/HubSpot, which have large integration
ecosystems.

`data_migration_tool` proved the underlying skills (connectors, field
mapping, DocType creation) but was a one-shot migration script, not a
reusable sync layer, and had structural issues (see §8). Frappe Connect
re-applies the same skills as a proper, extensible integration hub.

## 2. Goals / Non-Goals

**Goals**
- One consistent `Connector` interface any SaaS integration implements
- Two-way sync: push Frappe changes out, pull external changes in
- Installable as a normal Frappe app on any site
- Ship v1 with Google Sheets (done) + Slack (next)
- Portfolio-quality: tested, documented, demoable in under a minute

**Non-goals (v1)**
- Hosted/multi-tenant SaaS product — this installs on the user's own site
- No-code visual field-mapping UI — v1 mapping is a simple table
- Supporting every SaaS — 2-3 connectors is enough to prove the pattern

## 3. Users

- **Primary:** a Frappe/ERPNext admin who wants their site talking to
  another tool without hand-rolling a script each time
- **Secondary:** anyone technically evaluating this repo — a recruiter, a
  Marketplace reviewer, another Frappe developer deciding whether to add
  a connector

## 4. Functional Requirements

| ID | Requirement |
|----|-------------|
| FR1 | Admin creates a **Connector Configuration** (type, credentials, target DocType, field map) from the desk UI |
| FR2 | On create/update of a mapped DocType, the hub pushes the record out via a background job |
| FR3 | On a schedule, the hub pulls new/changed external records into Frappe |
| FR4 | Every sync run writes a **Sync Log** entry (status, count, errors) |
| FR5 | A non-Frappe system can push data in via a signed webhook |
| FR6 | Credentials are stored encrypted, never in plain config |

## 5. Non-Functional Requirements

- Retries only wrap genuinely transient failures, not permanent/config errors
- No silent failures — everything lands in Sync Log
- Adding a connector never requires touching the event engine or DocTypes
- Must not reintroduce any defect found in the `data_migration_tool` review (see §8)

---

## 6. High-Level Design

```
        Any Frappe app                                    Any SaaS
     (DocType create/update)                          (Sheets, Slack, ...)
              |                                                 ^
              v                                                 |
     doc_events hook (*)                                  Connector.push()
              |                                                 ^
              v                                                 |
      +-------------------------------------------------------------+
      |                      Frappe Connect (hub)                   |
      |                                                              |
      |   +-----------------+        +-------------------------+    |
      |   | Event Engine     |------->| Connector Registry       |   |
      |   | dispatcher.py    |        | { "Google Sheets": ... } |   |
      |   | webhooks.py      |<-------| connectors/*.py          |   |
      |   +-----------------+        +-------------------------+    |
      |            |                                                |
      |            v                                                |
      |   Connector Configuration / Connector Field Map / Sync Log  |
      |   / Connector Sync Map   (DocTypes, storage)                |
      +-------------------------------------------------------------+
              ^                                                 |
              |                                                 v
      inbound webhook (signed)                          scheduled pull
      external SaaS -> POST                              (cron, every 15 min)
```

**Three data paths:**

1. **Outbound (push):** DocType change → `doc_events` hook checks if any
   enabled Connector Configuration targets this DocType → enqueues a
   background job → `dispatcher.push_one()` → `Connector.push()` → Sync Log.
2. **Inbound scheduled (pull):** cron → `dispatcher.run_scheduled_pulls()`
   loops enabled configs → `Connector.pull(since=last_cursor)` → map fields
   → upsert into Frappe via `Connector Sync Map` → Sync Log.
3. **Inbound webhook:** external SaaS POSTs to `webhooks.receive` → HMAC
   signature verified against the connector's own secret → enqueued →
   same upsert path as #2 → Sync Log.

**Why a hub app instead of per-doctype code:** every doctype-to-SaaS pairing
is *configuration* (a Connector Configuration record), not code. This is
the difference from `data_migration_tool`, which hardcoded logic per
migration run instead of making the target configurable.

---

## 7. Low-Level Design

### 7.1 DocTypes

**Connector Configuration**

| Field | Type | Notes |
|---|---|---|
| connector_name | Data | unique, autoname |
| connector_type | Select | must match a key in `CONNECTOR_REGISTRY` |
| frappe_doctype | Link → DocType | the one DocType this config syncs |
| direction | Select | Pull / Push / Both |
| enabled | Check | default 1 |
| credentials | Password | JSON blob of secrets, encrypted at rest |
| webhook_secret | Password | HMAC key for inbound webhook |
| config | Code (JSON) | non-secret config, e.g. `spreadsheet_id` |
| field_map | Table → Connector Field Map | |
| last_cursor | Data | read-only, connector-defined cursor |
| last_synced_at | Datetime | read-only |

**Connector Field Map** (child table): `frappe_fieldname`, `external_fieldname`

**Sync Log**: `connector_configuration` (Link), `direction`, `status`
(Success / Partial Failure / Failed), `records_processed`, `error_count`,
`errors`, `started_at`, `ended_at`

**Connector Sync Map**: `connector_configuration` (Link), `frappe_doctype`,
`frappe_docname`, `external_id` — tracks which Frappe record corresponds
to which external record, **without requiring a custom field on
unrelated DocTypes**. (Trade-off: an extra lookup table instead of a
field on every synced doctype — chosen because it keeps the hub fully
non-invasive to whatever DocType it's pointed at.)

**Permissions:** System Manager only, on every DocType above.
*This is a direct fix for the review finding that `import_log.json`
referenced a `Data Migration User` role that was never defined anywhere
— that broke `bench migrate` on a clean install. v1 doesn't reference
any role it doesn't ship.*

### 7.2 Module layout

```
frappe_connect/
  hooks.py
  modules.txt
  patches.txt
  connectors/            <- unchanged from the step-1 delivery
    base.py  exceptions.py  retry.py  google_sheets.py
  event_engine/
    registry.py           connector_type -> class
    dispatcher.py          push/pull orchestration
    webhooks.py             inbound signed receiver
  frappe_connect/doctype/
    connector_configuration/
    connector_field_map/
    sync_log/
    connector_sync_map/
```

### 7.3 Error handling & retry policy

| Failure | Retried? | Where |
|---|---|---|
| Network/API rate limit during a single record write | Yes, 3x exponential backoff | `connectors/retry.py`, applied at the single-record-write level |
| Missing config (no header row, bad credentials) | No — raised immediately | validated before the retryable section |
| One record fails mid-batch | No retry of the whole batch | caught per-record, collected into `SyncResult.errors`, batch continues |
| Webhook signature invalid | No | rejected with `PermissionError`, never enqueued |

*This table exists because of a real bug the tests caught during the
connector build: `push()` originally retried the whole method, including
a permanent "no header row" error, three times for no benefit. Retry
scope is now explicit per failure type instead of applied blanket-wide.*

### 7.4 API surface

| Method | Whitelisted as | Auth |
|---|---|---|
| `event_engine.webhooks.receive` | `allow_guest=True`, POST only | HMAC-SHA256 signature per connector, not session auth |
| Everything else | desk-only, System Manager | standard Frappe session |

---

## 8. Review findings → how this design avoids them

| Found in `data_migration_tool` | Fix in Frappe Connect |
|---|---|
| Permission referenced an undefined role → broke `bench migrate` | Only System Manager used; no custom role shipped |
| `setup.py` referenced a missing `requirements.txt` | Single `pyproject.toml`, no `setup.py` |
| Dead code after a `return` in `api.py` | Every module covered by at least one test before being called done |
| `hooks.py` had a non-functional `api_methods` key | Only real hooks used (`doc_events`, `scheduler_events`), each one exercised by the dispatcher |
| README claimed Odoo support that didn't exist | README only documents what's implemented and tested |
| Zero tests anywhere | Every DocType ships a `test_*.py` stub; connector logic has real unit tests (7 passing today) |
| Files pushing 1,000–1,800 lines each | `dispatcher.py`/`webhooks.py`/`registry.py` kept single-purpose and short by design |

---

## 9. Milestones

- **M1 — done:** `Connector` interface + Google Sheets connector + 7 passing tests
- **M2 — this delivery:** DocTypes (Configuration / Field Map / Sync Log / Sync Map) + event engine skeleton (`dispatcher.py`, `registry.py`, `webhooks.py`)
- **M3:** Slack connector, added to `CONNECTOR_REGISTRY` — proves adding connector #2 is fast
- **M4:** Wire and test end-to-end on a real bench (outbound push on a real DocType, scheduled pull, webhook)
- **M5:** Full `bench new-app` packaging, demo GIF, LinkedIn post, Frappe Marketplace submission

## 10. Risks & Open Questions

- **Conflict resolution:** v1 is last-write-wins if both sides edit the same record — documented as a known limitation, not silently handled
- **Google API quota:** frequent per-record pushes could hit Sheets API limits at scale — batching is a v2 concern
- **Webhook replay protection:** v1 checks signature only, not a timestamp/nonce — acceptable for v1, flagged as a hardening item before production use
- **`Connector Sync Map` growth:** unbounded table growth for high-volume doctypes — needs an archiving strategy if this goes past a portfolio project

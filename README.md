### Frappe Connect

Native two-way sync hub connecting Frappe DocTypes to external SaaS tools
(Google Sheets, Slack, ...).

### The problem

Frappe/ERPNext sites today reach other SaaS tools (Zoho, WhatsApp, Google
Sheets, Slack, Shopify) either through paid iPaaS tools (Zapier, self-hosted
n8n) or one-off scripts nobody maintains. Unlike Salesforce/HubSpot, Frappe
has no open-source, native integration layer of its own. Frappe Connect is
that layer: install it on your site, add a config record, get two-way sync —
no custom script per integration.

### How it works

Three data paths, all driven by config, not code:

1. **Outbound (push).** A mapped DocType is created/updated → `doc_events`
   hook fires → matching enabled `Connector Configuration`s are found → a
   background job runs `Connector.push()` for that connector type → result
   written to `Sync Log`.
2. **Inbound scheduled (pull).** Every 15 min, cron walks enabled configs →
   `Connector.pull(since=last_cursor)` → external records mapped to Frappe
   fields → upserted via `Connector Sync Map` (tracks which Frappe record
   maps to which external record, without touching the target DocType's
   schema) → `Sync Log`.
3. **Inbound webhook.** External SaaS POSTs to `webhooks.receive` → HMAC-SHA256
   signature checked against the connector's own secret *before* anything is
   enqueued → same upsert path as #2 → `Sync Log`.

Adding connector #N is dropping one file in `connectors/` and self-registering
it with `@register("Type Name")` — the event engine, dispatcher, and DocTypes
never change. That single property is the point of the whole design: every
doctype-to-SaaS pairing is a `Connector Configuration` record, not hardcoded
logic, which is exactly what the predecessor one-shot migration script got
wrong.

Retries are scoped per failure type, not blanket: a `TransientConnectorError`
(rate limit, network blip) gets 3x exponential backoff; a
`PermanentConnectorError` (bad credentials, missing config) fails once and
lands in `Sync Log` immediately — regression-tested against a real bug where
a permanent config error was originally retried 3x for no benefit.

### Who it's for

- A Frappe/ERPNext admin who wants their site talking to another tool
  without hand-rolling and maintaining a script per integration.
- Anyone evaluating the codebase itself — another developer deciding
  whether to add a connector, or a Marketplace reviewer.

### Status

M1 (Connector interface + Google Sheets connector) and M2 (DocTypes + event
engine) are done — `bench migrate` clean, tests passing, `ruff` clean. Slack
connector (M3), a real end-to-end run against a live sheet (M4), and
packaging/Marketplace submission (M5) are not started. v1 is intentionally
narrow: 2-3 connectors to prove the pattern, no no-code mapping UI, no
hosted/multi-tenant mode — this installs on your own site. Full spec:
[`docs/PRD_HLD_LLD.md`](docs/PRD_HLD_LLD.md).

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app frappe_connect
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/frappe_connect
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit

import json

import frappe
from frappe.utils.password import get_decrypted_password

from frappe_connect.connectors.base import SyncResult
from frappe_connect.connectors.exceptions import ConnectorError
from frappe_connect.connectors.rate_limit import throttle
from frappe_connect.event_engine.registry import get_connector_class


def on_doc_change(doc, method=None):
	"""doc_events["*"] entrypoint - fires on every doctype, filters to matches."""
	configuration_names = frappe.get_all(
		"Connector Configuration",
		filters={"frappe_doctype": doc.doctype, "enabled": 1, "direction": ["in", ("Push", "Both")]},
		pluck="name",
	)
	for name in configuration_names:
		frappe.enqueue(
			"frappe_connect.event_engine.dispatcher.push_one",
			configuration_name=name,
			docname=doc.name,
			queue="short",
		)


def push_one(configuration_name, docname):
	"""Enqueued job: push a single Frappe document out through its connector."""
	started_at = frappe.utils.now_datetime()
	configuration = frappe.get_doc("Connector Configuration", configuration_name)
	result = SyncResult()

	try:
		doc = frappe.get_doc(configuration.frappe_doctype, docname)
		connector = _build_connector(configuration)
		mapped = _map_frappe_to_external(doc, configuration.field_map)
		throttle(configuration.name)
		result = connector.push([mapped])
	except ConnectorError as e:
		result.add_error({"docname": docname}, str(e))
	except Exception as e:
		frappe.log_error(title="Frappe Connect push failed", message=frappe.get_traceback())
		result.add_error({"docname": docname}, str(e))

	_write_sync_log(configuration.name, "Push", result, started_at)


@frappe.whitelist()
def retry_sync(sync_log_name):
	"""Re-run a failed/partial Sync Log entry from the Desk UI's "Retry Job" button."""
	log = frappe.get_doc("Sync Log", sync_log_name)
	if log.status == "Success":
		frappe.throw(frappe._("Nothing to retry - this sync already succeeded."))

	if log.direction == "Push":
		errors = json.loads(log.errors) if log.errors else []
		docname = errors[0].get("record", {}).get("docname") if errors else None
		if not docname:
			frappe.throw(frappe._("Cannot determine which record to retry."))
		frappe.enqueue(
			"frappe_connect.event_engine.dispatcher.push_one",
			configuration_name=log.connector_configuration,
			docname=docname,
			queue="short",
		)
	else:
		frappe.enqueue(
			"frappe_connect.event_engine.dispatcher.pull_one_by_name",
			configuration_name=log.connector_configuration,
			queue="short",
		)


def pull_one_by_name(configuration_name):
	"""frappe.enqueue target - pull_one itself takes an already-loaded doc, not a string."""
	pull_one(frappe.get_doc("Connector Configuration", configuration_name))


def run_scheduled_pulls():
	"""scheduler_events.cron entrypoint - pulls every enabled Pull/Both config."""
	configuration_names = frappe.get_all(
		"Connector Configuration",
		filters={"enabled": 1, "direction": ["in", ("Pull", "Both")]},
		pluck="name",
	)
	for name in configuration_names:
		pull_one(frappe.get_doc("Connector Configuration", name))


def pull_one(configuration):
	started_at = frappe.utils.now_datetime()
	result = SyncResult()

	try:
		connector = _build_connector(configuration)
		throttle(configuration.name)
		records, new_cursor = connector.pull(since=configuration.last_cursor or None)

		for record in records:
			try:
				mapped = _map_external_to_frappe(record, configuration)
				external_id = record.get("id") or record.get("external_id")
				upsert_record(configuration, mapped, external_id)
				result.records_processed += 1
			except Exception as e:
				result.add_error(record, str(e))

		if new_cursor is not None:
			# system-tracked sync cursor, not a user edit - skip modified/version bump
			configuration.db_set("last_cursor", new_cursor, update_modified=False)
		configuration.db_set("last_synced_at", frappe.utils.now_datetime(), update_modified=False)
	except ConnectorError as e:
		result.add_error({}, str(e))
	except Exception as e:
		frappe.log_error(title="Frappe Connect pull failed", message=frappe.get_traceback())
		result.add_error({}, str(e))

	_write_sync_log(configuration.name, "Pull", result, started_at)


def upsert_record(configuration, mapped_fields, external_id):
	"""Shared by scheduled pull and the inbound webhook - same upsert path either way."""
	existing_docname = frappe.db.get_value(
		"Connector Sync Map",
		{"connector_configuration": configuration.name, "external_id": external_id},
		"frappe_docname",
	)

	if existing_docname and frappe.db.exists(configuration.frappe_doctype, existing_docname):
		doc = frappe.get_doc(configuration.frappe_doctype, existing_docname)
		doc.update(mapped_fields)
		# external system is the source of truth for this write, not the current session user
		doc.save(ignore_permissions=True)
		return doc

	doc = frappe.get_doc({"doctype": configuration.frappe_doctype, **mapped_fields})
	doc.insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Connector Sync Map",
			"connector_configuration": configuration.name,
			"frappe_doctype": configuration.frappe_doctype,
			"frappe_docname": doc.name,
			"external_id": external_id,
		}
	).insert(ignore_permissions=True)
	return doc


def _build_connector(configuration):
	connector_class = get_connector_class(configuration.connector_type)
	config = json.loads(configuration.config) if configuration.config else {}
	credentials_raw = get_decrypted_password(
		"Connector Configuration", configuration.name, "credentials", raise_exception=False
	)
	credentials = json.loads(credentials_raw) if credentials_raw else {}
	return connector_class(config=config, credentials=credentials)


_HTML_FIELDTYPES = {"Text Editor", "HTML Editor"}


def _map_frappe_to_external(doc, field_map):
	mapped = {}
	for row in field_map:
		value = doc.get(row.frappe_fieldname)
		field = doc.meta.get_field(row.frappe_fieldname)
		if field and field.fieldtype in _HTML_FIELDTYPES and isinstance(value, str):
			value = frappe.utils.strip_html_tags(value).strip()
		mapped[row.external_fieldname] = value
	return mapped


_INT_FIELDTYPES = {"Int", "Check"}
_FLOAT_FIELDTYPES = {"Currency", "Float", "Percent"}


def _cast_value(value, fieldtype):
	"""External payloads (e.g. Google Sheets cell values) arrive as strings -
	cast to the target field's real type so numeric/date fields don't fail
	DocType validation on upsert.
	"""
	if value in (None, ""):
		return value
	try:
		if fieldtype in _INT_FIELDTYPES:
			return int(float(value))
		if fieldtype in _FLOAT_FIELDTYPES:
			return float(value)
		if fieldtype == "Date":
			return frappe.utils.getdate(value)
		if fieldtype == "Datetime":
			return frappe.utils.get_datetime(value)
	except (TypeError, ValueError) as e:
		raise ValueError(f"Cannot cast {value!r} to {fieldtype}: {e}")
	return value


def _map_external_to_frappe(record, configuration):
	meta = frappe.get_meta(configuration.frappe_doctype)
	mapped = {}
	for row in configuration.field_map:
		field = meta.get_field(row.frappe_fieldname)
		fieldtype = field.fieldtype if field else None
		mapped[row.frappe_fieldname] = _cast_value(record.get(row.external_fieldname), fieldtype)
	return mapped


def _write_sync_log(configuration_name, direction, result, started_at):
	# audit trail written by the system, independent of whoever's session triggered the sync
	frappe.get_doc(
		{
			"doctype": "Sync Log",
			"connector_configuration": configuration_name,
			"direction": direction,
			"status": result.status,
			"records_processed": result.records_processed,
			"error_count": result.error_count,
			"errors": json.dumps(result.errors) if result.errors else None,
			"started_at": started_at,
			"ended_at": frappe.utils.now_datetime(),
		}
	).insert(ignore_permissions=True)

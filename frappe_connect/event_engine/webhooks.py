import hashlib
import hmac
import json

import frappe
from frappe.utils.password import get_decrypted_password

from frappe_connect.connectors.base import SyncResult
from frappe_connect.event_engine.dispatcher import _map_external_to_frappe, _write_sync_log, upsert_record


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive(connector_name):
	"""Inbound signed webhook. Signature checked before anything is enqueued -
	an invalid signature is rejected outright, never retried, never queued.
	"""
	raw_body = frappe.request.get_data()
	signature = frappe.get_request_header("X-Frappe-Connect-Signature")

	configuration = frappe.get_doc("Connector Configuration", connector_name)
	secret = get_decrypted_password(
		"Connector Configuration", configuration.name, "webhook_secret", raise_exception=False
	)

	if not secret or not _valid_signature(raw_body, secret, signature):
		frappe.local.response.http_status_code = 403
		raise frappe.PermissionError("Invalid webhook signature")

	payload = json.loads(raw_body)
	frappe.enqueue(
		"frappe_connect.event_engine.webhooks.process_webhook_payload",
		connector_name=configuration.name,
		payload=payload,
		queue="short",
	)


def _valid_signature(raw_body, secret, signature):
	if not signature:
		return False
	expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
	return hmac.compare_digest(expected, signature)


def process_webhook_payload(connector_name, payload):
	started_at = frappe.utils.now_datetime()
	configuration = frappe.get_doc("Connector Configuration", connector_name)
	result = SyncResult()

	try:
		mapped = _map_external_to_frappe(payload, configuration)
		external_id = payload.get("id") or payload.get("external_id")
		upsert_record(configuration, mapped, external_id)
		result.records_processed = 1
	except Exception as e:
		frappe.log_error(title="Frappe Connect webhook processing failed", message=frappe.get_traceback())
		result.add_error(payload, str(e))

	_write_sync_log(configuration.name, "Pull", result, started_at)

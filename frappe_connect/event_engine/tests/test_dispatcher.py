import json
import unittest
from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_connect.connectors.base import Connector, SyncResult
from frappe_connect.event_engine import dispatcher
from frappe_connect.event_engine.registry import register


@register("Test Echo")
class EchoConnector(Connector):
	"""Registered only for these tests - echoes records back as success."""

	pushed: ClassVar[list] = []

	def push(self, records):
		EchoConnector.pushed.extend(records)
		return SyncResult(records_processed=len(records))

	def pull(self, since):
		return ([{"id": "ext-1", "description": "from external"}], "cursor-1")


class TestDispatcherPush(FrappeTestCase):
	def setUp(self):
		EchoConnector.pushed = []
		self.configuration = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Dispatcher Push Config",
				"connector_type": "Test Echo",
				"frappe_doctype": "ToDo",
				"direction": "Push",
				"field_map": [{"frappe_fieldname": "description", "external_fieldname": "desc"}],
			}
		).insert()
		self.todo = frappe.get_doc({"doctype": "ToDo", "description": "hello"}).insert()

	def tearDown(self):
		frappe.db.delete("Sync Log", {"connector_configuration": self.configuration.name})
		self.todo.delete()
		self.configuration.delete()

	def test_push_one_writes_success_sync_log(self):
		dispatcher.push_one(self.configuration.name, self.todo.name)

		self.assertEqual(EchoConnector.pushed, [{"desc": "hello"}])

		log = frappe.get_last_doc("Sync Log", filters={"connector_configuration": self.configuration.name})
		self.assertEqual(log.status, "Success")
		self.assertEqual(log.records_processed, 1)


class TestDispatcherPull(FrappeTestCase):
	def setUp(self):
		self.configuration = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Dispatcher Pull Config",
				"connector_type": "Test Echo",
				"frappe_doctype": "ToDo",
				"direction": "Pull",
				"field_map": [{"frappe_fieldname": "description", "external_fieldname": "description"}],
			}
		).insert()

	def tearDown(self):
		sync_map = frappe.db.get_value(
			"Connector Sync Map",
			{"connector_configuration": self.configuration.name},
			["name", "frappe_docname"],
		)
		frappe.db.delete("Sync Log", {"connector_configuration": self.configuration.name})
		if sync_map:
			name, docname = sync_map
			frappe.delete_doc("Connector Sync Map", name, force=True)
			frappe.delete_doc("ToDo", docname, force=True)
		self.configuration.delete()

	def test_pull_one_creates_record_and_sync_map(self):
		dispatcher.pull_one(self.configuration)

		sync_map = frappe.db.get_value(
			"Connector Sync Map",
			{"connector_configuration": self.configuration.name, "external_id": "ext-1"},
			"frappe_docname",
		)
		self.assertIsNotNone(sync_map)
		todo = frappe.get_doc("ToDo", sync_map)
		self.assertEqual(todo.description, "from external")

		self.configuration.reload()
		self.assertEqual(self.configuration.last_cursor, "cursor-1")


class TestRetrySync(FrappeTestCase):
	def setUp(self):
		self.configuration = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Retry Config",
				"connector_type": "Test Echo",
				"frappe_doctype": "ToDo",
				"direction": "Both",
			}
		).insert()

	def tearDown(self):
		frappe.db.delete("Sync Log", {"connector_configuration": self.configuration.name})
		self.configuration.delete()

	def _make_log(self, direction, status, errors=None):
		return frappe.get_doc(
			{
				"doctype": "Sync Log",
				"connector_configuration": self.configuration.name,
				"direction": direction,
				"status": status,
				"records_processed": 0,
				"error_count": len(errors) if errors else 0,
				"errors": json.dumps(errors) if errors else None,
			}
		).insert()

	def test_retry_rejects_success_log(self):
		log = self._make_log("Push", "Success")
		with self.assertRaises(frappe.ValidationError):
			dispatcher.retry_sync(log.name)

	def test_retry_push_requeues_same_docname(self):
		log = self._make_log("Push", "Failed", errors=[{"record": {"docname": "TODO-1"}, "message": "boom"}])
		with patch("frappe.enqueue") as mock_enqueue:
			dispatcher.retry_sync(log.name)

		mock_enqueue.assert_called_once_with(
			"frappe_connect.event_engine.dispatcher.push_one",
			configuration_name=self.configuration.name,
			docname="TODO-1",
			queue="short",
		)

	def test_retry_pull_requeues_configuration(self):
		log = self._make_log("Pull", "Failed", errors=[{"record": {}, "message": "boom"}])
		with patch("frappe.enqueue") as mock_enqueue:
			dispatcher.retry_sync(log.name)

		mock_enqueue.assert_called_once_with(
			"frappe_connect.event_engine.dispatcher.pull_one_by_name",
			configuration_name=self.configuration.name,
			queue="short",
		)


class TestCastValue(unittest.TestCase):
	"""Pure logic, no DB - external payloads are always strings, cast to the target fieldtype."""

	def test_passes_through_empty_values(self):
		self.assertIsNone(dispatcher._cast_value(None, "Currency"))
		self.assertEqual(dispatcher._cast_value("", "Int"), "")

	def test_casts_int_and_check(self):
		self.assertEqual(dispatcher._cast_value("42", "Int"), 42)
		self.assertEqual(dispatcher._cast_value("1", "Check"), 1)

	def test_casts_currency_float_percent(self):
		self.assertEqual(dispatcher._cast_value("150.00", "Currency"), 150.0)
		self.assertEqual(dispatcher._cast_value("3.5", "Float"), 3.5)
		self.assertEqual(dispatcher._cast_value("12", "Percent"), 12.0)

	def test_casts_date_and_datetime(self):
		self.assertEqual(dispatcher._cast_value("2026-01-15", "Date"), frappe.utils.getdate("2026-01-15"))
		self.assertEqual(
			dispatcher._cast_value("2026-01-15T10:30:00Z", "Datetime"),
			frappe.utils.get_datetime("2026-01-15T10:30:00Z"),
		)

	def test_leaves_unknown_fieldtype_untouched(self):
		self.assertEqual(dispatcher._cast_value("hello", "Data"), "hello")

	def test_raises_on_uncastable_value(self):
		with self.assertRaises(ValueError):
			dispatcher._cast_value("not-a-number", "Currency")


class TestMapExternalToFrappeCastsTypes(FrappeTestCase):
	def setUp(self):
		self.configuration = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Cast Config",
				"connector_type": "Test Echo",
				"frappe_doctype": "ToDo",
				"direction": "Pull",
				"field_map": [{"frappe_fieldname": "date", "external_fieldname": "due"}],
			}
		).insert()

	def tearDown(self):
		self.configuration.delete()

	def test_external_date_string_cast_to_date_object(self):
		mapped = dispatcher._map_external_to_frappe({"due": "2026-01-15"}, self.configuration)
		self.assertEqual(mapped["date"], frappe.utils.getdate("2026-01-15"))


class TestOnDocChangeFiresOnce(FrappeTestCase):
	"""Regression test: a real doc.insert() fires both after_insert and on_update
	(run_post_save_methods runs on_update for the "save" action, which includes
	inserts) - doc_events registering on_doc_change on both hooks double-pushed
	every new record. Caught live via the Desk UI: a single new ToDo produced
	two rows in the target Google Sheet. hooks.py now wires on_doc_change to
	on_update only.
	"""

	def setUp(self):
		self.configuration = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Hook Fire Once Config",
				"connector_type": "Test Echo",
				"frappe_doctype": "ToDo",
				"direction": "Push",
			}
		).insert()

	def tearDown(self):
		self.configuration.delete()

	def test_creating_a_doc_enqueues_push_exactly_once(self):
		# Isolate on this test's own configuration_name - a shared dev DB can have
		# other real Connector Configurations also targeting ToDo/Push, and each
		# is correctly entitled to its own enqueue.
		with patch("frappe.enqueue") as mock_enqueue:
			todo = frappe.get_doc({"doctype": "ToDo", "description": "hook fire once"}).insert()
		try:
			calls_for_this_config = [
				c
				for c in mock_enqueue.call_args_list
				if c.kwargs.get("configuration_name") == self.configuration.name
			]
			self.assertEqual(len(calls_for_this_config), 1)
		finally:
			todo.delete()

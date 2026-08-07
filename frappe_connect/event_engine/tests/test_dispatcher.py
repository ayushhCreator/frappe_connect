from typing import ClassVar

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

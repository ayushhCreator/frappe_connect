# Copyright (c) 2026, Ayush and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestConnectorSyncMap(FrappeTestCase):
	def test_creation(self):
		config = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Connector For Sync Map",
				"connector_type": "Google Sheets",
				"frappe_doctype": "ToDo",
				"direction": "Pull",
			}
		).insert()

		sync_map = frappe.get_doc(
			{
				"doctype": "Connector Sync Map",
				"connector_configuration": config.name,
				"frappe_doctype": "ToDo",
				"frappe_docname": "some-todo-name",
				"external_id": "ext-123",
			}
		).insert()

		self.assertEqual(sync_map.external_id, "ext-123")
		sync_map.delete()
		config.delete()

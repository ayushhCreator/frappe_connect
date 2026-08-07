# Copyright (c) 2026, Ayush and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestSyncLog(FrappeTestCase):
	def test_creation(self):
		config = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Connector For Log",
				"connector_type": "Google Sheets",
				"frappe_doctype": "ToDo",
				"direction": "Push",
			}
		).insert()

		log = frappe.get_doc(
			{
				"doctype": "Sync Log",
				"connector_configuration": config.name,
				"direction": "Push",
				"status": "Success",
				"records_processed": 1,
				"error_count": 0,
			}
		).insert()

		self.assertEqual(log.status, "Success")
		log.delete()
		config.delete()

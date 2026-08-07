# Copyright (c) 2026, Ayush and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestConnectorConfiguration(FrappeTestCase):
	def test_creation(self):
		doc = frappe.get_doc(
			{
				"doctype": "Connector Configuration",
				"connector_name": "Test Connector",
				"connector_type": "Google Sheets",
				"frappe_doctype": "ToDo",
				"direction": "Push",
			}
		).insert()
		self.assertEqual(doc.name, "Test Connector")
		doc.delete()

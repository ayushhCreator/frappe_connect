import unittest

from frappe_connect.event_engine.registry import CONNECTOR_REGISTRY, get_connector_class, register


class TestRegistry(unittest.TestCase):
	def test_register_and_lookup(self):
		@register("Unit Test Connector")
		class _Stub:
			pass

		try:
			self.assertIs(get_connector_class("Unit Test Connector"), _Stub)
		finally:
			del CONNECTOR_REGISTRY["Unit Test Connector"]

	def test_unknown_type_raises(self):
		with self.assertRaises(KeyError):
			get_connector_class("Nonexistent Type")

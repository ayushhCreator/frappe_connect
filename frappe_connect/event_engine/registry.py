import importlib
import pkgutil

import frappe

from frappe_connect import connectors

CONNECTOR_REGISTRY = {}


@frappe.whitelist()
def get_connector_types():
	"""Desk-only: powers Connector Configuration's connector_type dropdown.

	Registry keys, not a stored list - adding connector #N never touches
	this or the DocType JSON.
	"""
	return list(CONNECTOR_REGISTRY.keys())


def register(connector_type):
	"""Class decorator: @register("Google Sheets") on a Connector subclass.

	Adding connector #N is dropping a new file in connectors/ with this
	decorator - nothing here or in the DocTypes needs editing.
	"""

	def decorator(cls):
		CONNECTOR_REGISTRY[connector_type] = cls
		return cls

	return decorator


def get_connector_class(connector_type):
	if connector_type not in CONNECTOR_REGISTRY:
		raise KeyError(f"No connector registered for type '{connector_type}'")
	return CONNECTOR_REGISTRY[connector_type]


def _discover_connectors():
	"""Import every top-level module under connectors/ so its @register runs."""
	for _finder, name, _is_pkg in pkgutil.iter_modules(connectors.__path__, connectors.__name__ + "."):
		if name.rsplit(".", 1)[-1] == "tests":
			continue
		importlib.import_module(name)


_discover_connectors()

import importlib
import pkgutil

from frappe_connect import connectors

CONNECTOR_REGISTRY = {}


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

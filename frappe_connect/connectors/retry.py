import functools
import time

from frappe_connect.connectors.exceptions import TransientConnectorError


def with_retry(max_attempts=3, backoff_base=1.0, sleep_fn=time.sleep):
	"""Retry a single-record write on TransientConnectorError only.

	Any other exception (including PermanentConnectorError) is raised
	immediately on the first attempt - retrying a config/auth error wastes
	time and hides the real problem.
	"""

	def decorator(fn):
		@functools.wraps(fn)
		def wrapper(*args, **kwargs):
			attempt = 0
			while True:
				attempt += 1
				try:
					return fn(*args, **kwargs)
				except TransientConnectorError:
					if attempt >= max_attempts:
						raise
					sleep_fn(backoff_base * (2 ** (attempt - 1)))

		return wrapper

	return decorator

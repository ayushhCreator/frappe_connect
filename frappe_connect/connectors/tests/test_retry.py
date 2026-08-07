import unittest

from frappe_connect.connectors.exceptions import PermanentConnectorError, TransientConnectorError
from frappe_connect.connectors.retry import with_retry


class TestWithRetry(unittest.TestCase):
	def test_retries_transient_error_until_success(self):
		calls = []

		@with_retry(max_attempts=3, sleep_fn=lambda _seconds: None)
		def flaky():
			calls.append(1)
			if len(calls) < 3:
				raise TransientConnectorError("rate limited")
			return "ok"

		self.assertEqual(flaky(), "ok")
		self.assertEqual(len(calls), 3)

	def test_raises_after_max_attempts(self):
		calls = []

		@with_retry(max_attempts=3, sleep_fn=lambda _seconds: None)
		def always_fails():
			calls.append(1)
			raise TransientConnectorError("still down")

		with self.assertRaises(TransientConnectorError):
			always_fails()
		self.assertEqual(len(calls), 3)

	def test_permanent_error_is_not_retried(self):
		calls = []

		@with_retry(max_attempts=3, sleep_fn=lambda _seconds: None)
		def bad_config():
			calls.append(1)
			raise PermanentConnectorError("missing header row")

		with self.assertRaises(PermanentConnectorError):
			bad_config()
		self.assertEqual(len(calls), 1)

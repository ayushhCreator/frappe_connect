import unittest

from frappe_connect.connectors.base import SyncResult


class TestSyncResult(unittest.TestCase):
	def test_status_success_when_no_errors(self):
		result = SyncResult(records_processed=5)
		self.assertEqual(result.status, "Success")
		self.assertEqual(result.error_count, 0)

	def test_status_partial_failure_when_some_succeed(self):
		result = SyncResult(records_processed=3)
		result.add_error({"id": 1}, "timeout")
		self.assertEqual(result.status, "Partial Failure")
		self.assertEqual(result.error_count, 1)

	def test_status_failed_when_nothing_succeeds(self):
		result = SyncResult(records_processed=0)
		result.add_error({"id": 1}, "bad credentials")
		self.assertEqual(result.status, "Failed")

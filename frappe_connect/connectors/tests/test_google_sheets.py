import unittest
from unittest.mock import MagicMock, patch

from googleapiclient.errors import HttpError

from frappe_connect.connectors.exceptions import PermanentConnectorError
from frappe_connect.connectors.google_sheets import GoogleSheetsConnector


def _connector():
	return GoogleSheetsConnector(
		config={"spreadsheet_id": "sheet-123", "sheet_name": "Sheet1"},
		credentials={"client_email": "x", "private_key": "y", "token_uri": "z"},
	)


class TestGoogleSheetsConnectorConfig(unittest.TestCase):
	def test_missing_spreadsheet_id_raises_permanent_error(self):
		with self.assertRaises(PermanentConnectorError):
			GoogleSheetsConnector(config={}, credentials={"a": "b"})

	def test_missing_credentials_raises_permanent_error(self):
		with self.assertRaises(PermanentConnectorError):
			GoogleSheetsConnector(config={"spreadsheet_id": "x"}, credentials={})


class TestGoogleSheetsConnectorApi(unittest.TestCase):
	@patch("frappe_connect.connectors.google_sheets.build")
	@patch("frappe_connect.connectors.google_sheets.Credentials")
	def test_missing_header_row_fails_immediately_not_retried(self, _mock_creds, mock_build):
		service = MagicMock()
		mock_build.return_value = service
		get_execute = service.spreadsheets.return_value.values.return_value.get.return_value.execute
		get_execute.return_value = {"values": []}

		connector = _connector()
		with self.assertRaises(PermanentConnectorError):
			connector.push([{"desc": "hello"}])

		# one attempt only - a missing header is a config error, not a transient one
		self.assertEqual(get_execute.call_count, 1)

	@patch("frappe_connect.connectors.google_sheets.build")
	@patch("frappe_connect.connectors.google_sheets.Credentials")
	def test_push_appends_mapped_row_per_header_column(self, _mock_creds, mock_build):
		service = MagicMock()
		mock_build.return_value = service
		get_execute = service.spreadsheets.return_value.values.return_value.get.return_value.execute
		get_execute.return_value = {"values": [["desc", "status"]]}
		append_execute = service.spreadsheets.return_value.values.return_value.append.return_value.execute
		append_execute.return_value = {}

		connector = _connector()
		result = connector.push([{"desc": "hello", "status": "Open"}])

		self.assertEqual(result.status, "Success")
		self.assertEqual(result.records_processed, 1)
		body = service.spreadsheets.return_value.values.return_value.append.call_args.kwargs["body"]
		self.assertEqual(body["values"], [["hello", "Open"]])

	@patch("frappe_connect.connectors.retry.time.sleep")
	@patch("frappe_connect.connectors.google_sheets.build")
	@patch("frappe_connect.connectors.google_sheets.Credentials")
	def test_transient_append_error_is_retried_then_succeeds(self, _mock_creds, mock_build, _mock_sleep):
		service = MagicMock()
		mock_build.return_value = service
		get_execute = service.spreadsheets.return_value.values.return_value.get.return_value.execute
		get_execute.return_value = {"values": [["desc"]]}
		append_execute = service.spreadsheets.return_value.values.return_value.append.return_value.execute
		append_execute.side_effect = [HttpError(MagicMock(status=503), b"unavailable"), {}]

		connector = _connector()
		result = connector.push([{"desc": "hello"}])

		self.assertEqual(result.status, "Success")
		self.assertEqual(append_execute.call_count, 2)

	@patch("frappe_connect.connectors.google_sheets.build")
	@patch("frappe_connect.connectors.google_sheets.Credentials")
	def test_permanent_append_error_does_not_abort_batch(self, _mock_creds, mock_build):
		service = MagicMock()
		mock_build.return_value = service
		get_execute = service.spreadsheets.return_value.values.return_value.get.return_value.execute
		get_execute.return_value = {"values": [["desc"]]}
		append_execute = service.spreadsheets.return_value.values.return_value.append.return_value.execute
		append_execute.side_effect = [HttpError(MagicMock(status=400), b"bad request"), {}]

		connector = _connector()
		result = connector.push([{"desc": "bad"}, {"desc": "good"}])

		self.assertEqual(result.status, "Partial Failure")
		self.assertEqual(result.records_processed, 1)
		self.assertEqual(result.error_count, 1)

	@patch("frappe_connect.connectors.google_sheets.build")
	@patch("frappe_connect.connectors.google_sheets.Credentials")
	def test_pull_maps_rows_to_header_columns(self, _mock_creds, mock_build):
		service = MagicMock()
		mock_build.return_value = service
		get_execute = service.spreadsheets.return_value.values.return_value.get.return_value.execute
		get_execute.side_effect = [
			{"values": [["desc", "status"]]},
			{"values": [["desc", "status"], ["hello", "Open"], ["short row"]]},
		]

		connector = _connector()
		records, cursor = connector.pull(since=None)

		self.assertEqual(
			records,
			[{"desc": "hello", "status": "Open"}, {"desc": "short row", "status": ""}],
		)
		self.assertIsNone(cursor)

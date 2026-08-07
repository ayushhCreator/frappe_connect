from google.auth.exceptions import GoogleAuthError
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from frappe_connect.connectors.base import Connector, SyncResult
from frappe_connect.connectors.exceptions import PermanentConnectorError, TransientConnectorError
from frappe_connect.connectors.retry import with_retry
from frappe_connect.event_engine.registry import register

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_RETRYABLE_STATUS = {429, 500, 502, 503}


@register("Google Sheets")
class GoogleSheetsConnector(Connector):
	"""credentials = a Google service-account JSON key, config = {"spreadsheet_id", "sheet_name"}."""

	def __init__(self, config, credentials):
		super().__init__(config, credentials)
		self.spreadsheet_id = config.get("spreadsheet_id")
		self.sheet_name = config.get("sheet_name", "Sheet1")
		if not self.spreadsheet_id:
			raise PermanentConnectorError("config.spreadsheet_id is required")
		if not credentials:
			raise PermanentConnectorError("credentials (service account JSON key) is required")

	def push(self, records: list[dict]) -> SyncResult:
		service = self._service()
		# missing header is a config error - fails immediately, never retried
		header = self._header_row(service)

		result = SyncResult()
		for record in records:
			try:
				row = [record.get(column, "") for column in header]
				self._append_row(service, row)
				result.records_processed += 1
			except (TransientConnectorError, PermanentConnectorError) as e:
				result.add_error(record, str(e))
		return result

	def pull(self, since: str | None) -> tuple[list[dict], str | None]:
		service = self._service()
		header = self._header_row(service)
		response = self._get_values(service, self.sheet_name)
		data_rows = response.get("values", [])[1:]

		records = []
		for row in data_rows:
			padded = row + [""] * (len(header) - len(row))
			records.append(dict(zip(header, padded, strict=False)))

		# ponytail: Sheets has no native change-cursor, so this is a full-sheet
		# scan every pull; Connector Sync Map dedupes. Fine at portfolio scale,
		# revisit (delta ranges / row hashing) if this needs to handle big sheets.
		return records, since

	def _service(self):
		try:
			creds = Credentials.from_service_account_info(self.credentials, scopes=SCOPES)
			return build("sheets", "v4", credentials=creds, cache_discovery=False)
		except (ValueError, GoogleAuthError) as e:
			raise PermanentConnectorError(f"Invalid service account credentials: {e}") from e

	def _header_row(self, service):
		response = self._get_values(service, f"{self.sheet_name}!1:1")
		rows = response.get("values", [])
		if not rows or not rows[0]:
			raise PermanentConnectorError(f"Sheet '{self.sheet_name}' has no header row")
		return rows[0]

	@with_retry()
	def _get_values(self, service, range_):
		try:
			return (
				service.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range=range_).execute()
			)
		except HttpError as e:
			raise _classify(e) from e

	@with_retry()
	def _append_row(self, service, row):
		try:
			service.spreadsheets().values().append(
				spreadsheetId=self.spreadsheet_id,
				range=self.sheet_name,
				valueInputOption="RAW",
				insertDataOption="INSERT_ROWS",
				body={"values": [row]},
			).execute()
		except HttpError as e:
			raise _classify(e) from e


def _classify(http_error: HttpError):
	status = http_error.resp.status
	if status in _RETRYABLE_STATUS:
		return TransientConnectorError(str(http_error))
	return PermanentConnectorError(str(http_error))

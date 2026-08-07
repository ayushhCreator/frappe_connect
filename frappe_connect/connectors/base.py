from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SyncResult:
	"""Outcome of one push or pull run, mapped 1:1 onto a Sync Log entry."""

	records_processed: int = 0
	errors: list[dict] = field(default_factory=list)

	def add_error(self, record: dict, message: str):
		self.errors.append({"record": record, "message": message})

	@property
	def error_count(self) -> int:
		return len(self.errors)

	@property
	def status(self) -> str:
		if not self.errors:
			return "Success"
		if self.records_processed == 0:
			return "Failed"
		return "Partial Failure"


class Connector(ABC):
	"""One consistent interface every SaaS integration implements.

	config: non-secret settings from Connector Configuration.config (JSON)
	credentials: decrypted secrets from Connector Configuration.credentials
	"""

	def __init__(self, config: dict, credentials: dict):
		self.config = config
		self.credentials = credentials

	@abstractmethod
	def push(self, records: list[dict]) -> SyncResult:
		"""Write already field-mapped records to the external system.

		Validate config/credentials up front and raise PermanentConnectorError
		immediately - do not let per-record retry mask a broken setup.
		Per-record write failures are caught by the implementation and
		collected into SyncResult.errors; one bad record must not abort
		the rest of the batch.
		"""

	@abstractmethod
	def pull(self, since: str | None) -> tuple[list[dict], str | None]:
		"""Fetch external records changed since the given cursor.

		Returns (raw_external_records, new_cursor). Field mapping and
		Frappe upsert happen in the dispatcher, not here.
		"""

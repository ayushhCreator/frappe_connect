class ConnectorError(Exception):
	"""Base error for all connector failures."""


class TransientConnectorError(ConnectorError):
	"""Retryable failure: network blip, rate limit, timeout."""


class PermanentConnectorError(ConnectorError):
	"""Non-retryable failure: bad config, bad credentials, malformed data."""

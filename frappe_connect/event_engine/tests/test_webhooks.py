import hashlib
import hmac
import unittest

from frappe_connect.event_engine.webhooks import _valid_signature


class TestValidSignature(unittest.TestCase):
	def test_valid_signature_accepted(self):
		secret = "shh"
		body = b'{"id": 1}'
		signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		self.assertTrue(_valid_signature(body, secret, signature))

	def test_wrong_signature_rejected(self):
		self.assertFalse(_valid_signature(b'{"id": 1}', "shh", "deadbeef"))

	def test_missing_signature_rejected(self):
		self.assertFalse(_valid_signature(b'{"id": 1}', "shh", None))

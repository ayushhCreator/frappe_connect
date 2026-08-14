import unittest

from frappe_connect.connectors.rate_limit import throttle


class FakeCache:
	"""In-memory stand-in for frappe.cache() - just the two redis calls throttle() uses."""

	def __init__(self):
		self.store = {}

	def incrby(self, key, amount):
		self.store[key] = self.store.get(key, 0) + amount
		return self.store[key]

	def expire(self, key, seconds):
		pass


class TestThrottle(unittest.TestCase):
	def setUp(self):
		self.cache = FakeCache()
		self.sleeps = []
		self.now = 1000.0

	def _time(self):
		return self.now

	def test_allows_calls_under_limit_without_sleeping(self):
		for _ in range(3):
			throttle(
				"config-a",
				max_calls=3,
				period_seconds=60,
				cache=self.cache,
				sleep_fn=self.sleeps.append,
				time_fn=self._time,
			)
		self.assertEqual(self.sleeps, [])

	def test_blocks_once_limit_exceeded(self):
		for _ in range(2):
			throttle(
				"config-a",
				max_calls=1,
				period_seconds=60,
				cache=self.cache,
				sleep_fn=self.sleeps.append,
				time_fn=self._time,
			)
		self.assertEqual(len(self.sleeps), 1)

	def test_different_keys_have_independent_limits(self):
		throttle(
			"config-a",
			max_calls=1,
			period_seconds=60,
			cache=self.cache,
			sleep_fn=self.sleeps.append,
			time_fn=self._time,
		)
		throttle(
			"config-b",
			max_calls=1,
			period_seconds=60,
			cache=self.cache,
			sleep_fn=self.sleeps.append,
			time_fn=self._time,
		)
		self.assertEqual(self.sleeps, [])

import time

import frappe


def throttle(key, max_calls=60, period_seconds=60, cache=None, sleep_fn=time.sleep, time_fn=time.time):
	"""Blocks the caller until a call slot is free for `key`.

	Fixed-window counter in Frappe's shared redis cache - safe across
	multiple RQ workers hitting the same external API concurrently. Call
	right before an outbound connector.push()/pull() so a burst of jobs
	(e.g. a bulk doc update) can't blow through Slack/Google's per-key quota.

	ponytail: fixed window, not sliding - a burst straddling a window
	boundary can briefly exceed max_calls. Upgrade to a sliding window if a
	real quota gets tripped in practice.
	"""
	cache = cache or frappe.cache()
	window = int(time_fn()) // period_seconds
	cache_key = f"frappe_connect:rate:{key}:{window}"

	count = cache.incrby(cache_key, 1)
	if count == 1:
		cache.expire(cache_key, period_seconds)

	if count > max_calls:
		wait = period_seconds - (int(time_fn()) % period_seconds)
		sleep_fn(wait)

import unittest
from unittest.mock import Mock, patch

import app as validator


def api_response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class ResponseCacheTests(unittest.TestCase):
    def setUp(self):
        with validator._cache_lock:
            validator._response_cache.clear()
        self.client = validator.app.test_client()

    def test_search_results_are_cached_and_refresh_bypasses_cache(self):
        response = api_response({
            "search": [{"id": "Q42", "label": "Douglas Adams"}]
        })

        with patch.object(validator.requests, "get", return_value=response) as get:
            first = self.client.get("/search?q=Douglas+Adams")
            second = self.client.get("/search?q=Douglas+Adams")
            refreshed = self.client.get("/search?q=Douglas+Adams&refresh=1")

        self.assertEqual(first.get_json(), [{"id": "Q42", "label": "Douglas Adams", "description": ""}])
        self.assertEqual(second.get_json(), first.get_json())
        self.assertEqual(refreshed.get_json(), first.get_json())
        self.assertEqual(get.call_count, 2)

    def test_labels_are_cached_and_refresh_bypasses_cache(self):
        response = api_response({
            "entities": {
                "P31": {"labels": {"en": {"value": "instance of"}}}
            }
        })

        with patch.object(validator.requests, "get", return_value=response) as get:
            first = validator.fetch_labels({"P31"})
            second = validator.fetch_labels({"P31"})
            refreshed = validator.fetch_labels({"P31"}, refresh=True)

        self.assertEqual(first, {"P31": "instance of"})
        self.assertEqual(second, first)
        self.assertEqual(refreshed, first)
        self.assertEqual(get.call_count, 2)

    def test_wayback_snapshots_are_cached_and_refresh_bypasses_cache(self):
        response = api_response({
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://web.archive.org/example",
                    "timestamp": "20240101000000",
                    "status": "200"
                }
            }
        })

        with patch.object(validator.requests, "get", return_value=response) as get:
            first = validator.fetch_wayback_snapshot("https://example.org")
            second = validator.fetch_wayback_snapshot("https://example.org")
            refreshed = validator.fetch_wayback_snapshot("https://example.org", refresh=True)

        self.assertEqual(first, second)
        self.assertEqual(refreshed, first)
        self.assertEqual(get.call_count, 2)

    def test_expired_entries_are_misses(self):
        with patch.object(validator.time, "monotonic", return_value=100):
            validator._cache_set(("test", "expired"), "value")

        with patch.object(validator.time, "monotonic", return_value=100 + validator.CACHE_TTL_SECONDS):
            cached = validator._cache_get(("test", "expired"))

        self.assertIs(cached, validator._CACHE_MISS)

    def test_cache_size_is_bounded(self):
        for index in range(validator.CACHE_MAX_ENTRIES + 1):
            validator._cache_set(("test", str(index)), index)

        self.assertEqual(len(validator._response_cache), validator.CACHE_MAX_ENTRIES)
        self.assertIs(
            validator._cache_get(("test", "0")),
            validator._CACHE_MISS
        )


if __name__ == "__main__":
    unittest.main()

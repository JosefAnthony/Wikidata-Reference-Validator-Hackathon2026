"""Tests for the TTL caches around Wikidata search, labels and Wayback lookups."""

import asyncio
import unittest
from unittest.mock import patch

import app


class FakeResponse:
    """Just enough of a requests.Response for the code under test."""

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeClock:
    """Replaces time.monotonic() so expiry can be tested without waiting."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeAiohttpResponse:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeAiohttpSession:
    """Stands in for aiohttp.ClientSession in the Wayback lookup."""

    def __init__(self, status=200, data=None, error=None):
        self.status = status
        self.data = data
        self.error = error
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return FakeAiohttpResponse(self.status, self.data)


def wayback(session, url, refresh=False):
    async def run():
        return await app.fetch_wayback_snapshot_async(session, asyncio.Semaphore(1), url, refresh)
    return asyncio.run(run())


SNAPSHOT = {
    "archived_snapshots": {
        "closest": {
            "available": True,
            "url": "http://web.archive.org/web/20200101000000/https://dead.test/",
            "timestamp": "20200101000000",
            "status": "200",
        }
    }
}


class TTLCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = patch("app.time.monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_value_until_it_expires(self):
        cache = app.TTLCache(ttl=60)
        cache.set("k", "v")

        self.clock.now += 59
        self.assertEqual(cache.get("k"), "v")

        self.clock.now += 1
        self.assertIs(cache.get("k"), app.MISSING)
        self.assertEqual(len(cache), 0)

    def test_none_is_a_cacheable_value(self):
        cache = app.TTLCache(ttl=60)
        cache.set("k", None)
        self.assertIsNone(cache.get("k"))

    def test_size_is_bounded_and_least_recently_used_is_dropped(self):
        cache = app.TTLCache(ttl=60, maxsize=3)
        for key in "abc":
            cache.set(key, key)
        cache.get("a")          # "a" is now the most recently used
        cache.set("d", "d")     # so "b" is dropped

        self.assertEqual(len(cache), 3)
        self.assertIs(cache.get("b"), app.MISSING)
        for key in "acd":
            self.assertEqual(cache.get(key), key)

    def test_never_grows_past_maxsize(self):
        cache = app.TTLCache(ttl=60, maxsize=500)
        for i in range(1200):
            cache.set(i, i)
        self.assertEqual(len(cache), 500)

    def test_per_entry_ttl_overrides_default(self):
        cache = app.TTLCache(ttl=600)
        cache.set("short", 1, ttl=10)
        self.clock.now += 10
        self.assertIs(cache.get("short"), app.MISSING)

    def test_zero_ttl_disables_caching(self):
        cache = app.TTLCache(ttl=0)
        cache.set("k", "v")
        self.assertIs(cache.get("k"), app.MISSING)


class EnvSettingTests(unittest.TestCase):
    def test_invalid_or_negative_values_use_the_default(self):
        for value in ("abc", "-5", ""):
            with patch.dict("os.environ", {"CACHE_TEST_SETTING": value}):
                self.assertEqual(app._env_int("CACHE_TEST_SETTING", 42), 42)

    def test_valid_value_is_used(self):
        with patch.dict("os.environ", {"CACHE_TEST_SETTING": "7"}):
            self.assertEqual(app._env_int("CACHE_TEST_SETTING", 42), 7)


class SearchCacheTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    @patch("app.requests.get")
    def test_repeated_search_is_served_from_cache(self, get):
        get.return_value = FakeResponse({"search": [{"id": "Q42", "label": "Douglas Adams"}]})

        first = self.client.get("/search?q=Douglas Adams")
        second = self.client.get("/search?q=  douglas   ADAMS ")

        self.assertEqual(get.call_count, 1)
        self.assertEqual(first.get_json(), second.get_json())

    @patch("app.requests.get")
    def test_cache_is_per_language(self, get):
        get.return_value = FakeResponse({"search": [{"id": "Q90", "label": "Paris"}]})

        self.client.get("/search?q=Paris&language=en")
        self.client.get("/search?q=Paris&language=fr")

        self.assertEqual(get.call_count, 2)

    @patch("app.requests.get")
    def test_refresh_bypasses_and_updates_the_cache(self, get):
        get.side_effect = [
            FakeResponse({"search": [{"id": "Q1", "label": "Old"}]}),
            FakeResponse({"search": [{"id": "Q1", "label": "New"}]}),
        ]

        self.client.get("/search?q=thing")
        refreshed = self.client.get("/search?q=thing&refresh=1")
        after = self.client.get("/search?q=thing")

        self.assertEqual(get.call_count, 2)
        self.assertEqual(refreshed.get_json()[0]["label"], "New")
        self.assertEqual(after.get_json()[0]["label"], "New")

    @patch("app.requests.get")
    def test_expired_search_is_fetched_again(self, get):
        get.return_value = FakeResponse({"search": []})
        clock = FakeClock()
        with patch("app.time.monotonic", clock):
            self.client.get("/search?q=Paris")
            clock.now += app.SEARCH_CACHE.ttl
            self.client.get("/search?q=Paris")

        self.assertEqual(get.call_count, 2)

    @patch("app.requests.get")
    def test_failures_are_not_cached(self, get):
        get.side_effect = [
            RuntimeError("network down"),
            FakeResponse({"search": [{"id": "Q90", "label": "Paris"}]}),
        ]

        self.assertEqual(self.client.get("/search?q=Paris").status_code, 502)
        self.assertEqual(self.client.get("/search?q=Paris").status_code, 200)

    @patch("app.requests.get")
    def test_result_with_failed_label_lookup_is_not_cached(self, get):
        get.side_effect = [
            FakeResponse({"search": [{"id": "Q90"}]}),          # no label in this language
            RuntimeError("label lookup failed"),
            FakeResponse({"search": [{"id": "Q90"}]}),
            FakeResponse({"entities": {"Q90": {"labels": {"en": {"value": "Paris"}}}}}),
        ]

        first = self.client.get("/search?q=Paris")
        second = self.client.get("/search?q=Paris")

        self.assertEqual(first.get_json()[0]["label"], "Q90")
        self.assertEqual(second.get_json()[0]["label"], "Paris")
        self.assertEqual(get.call_count, 4)


class LabelCacheTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()

    @patch("app.requests.get")
    def test_only_uncached_labels_are_requested(self, get):
        get.side_effect = [
            FakeResponse({"entities": {"P31": {"labels": {"en": {"value": "instance of"}}}}}),
            FakeResponse({"entities": {"P17": {"labels": {"en": {"value": "country"}}}}}),
        ]

        self.assertEqual(app.fetch_labels({"P31"}), {"P31": "instance of"})
        labels = app.fetch_labels({"P31", "P17"})

        self.assertEqual(labels, {"P31": "instance of", "P17": "country"})
        self.assertEqual(get.call_args_list[1].kwargs["params"]["ids"], "P17")

    @patch("app.requests.get")
    def test_fully_cached_lookup_makes_no_request(self, get):
        get.return_value = FakeResponse({"entities": {
            "P31": {"labels": {"en": {"value": "instance of"}}},
            "Q5": {"labels": {}},  # no label: remembered as such
        }})

        app.fetch_labels({"P31", "Q5"})
        labels = app.fetch_labels({"P31", "Q5"})

        self.assertEqual(labels, {"P31": "instance of"})
        self.assertEqual(get.call_count, 1)

    @patch("app.requests.get")
    def test_labels_are_cached_per_language(self, get):
        get.side_effect = [
            FakeResponse({"entities": {"P17": {"labels": {"en": {"value": "country"}}}}}),
            FakeResponse({"entities": {"P17": {"labels": {"fr": {"value": "pays"}}}}}),
        ]

        self.assertEqual(app.fetch_labels({"P17"}, "en"), {"P17": "country"})
        self.assertEqual(app.fetch_labels({"P17"}, "fr"), {"P17": "pays"})
        self.assertEqual(get.call_count, 2)

    @patch("app.requests.get")
    def test_refresh_requests_every_label(self, get):
        get.return_value = FakeResponse({"entities": {"P31": {"labels": {"en": {"value": "instance of"}}}}})

        app.fetch_labels({"P31"})
        app.fetch_labels({"P31"}, refresh=True)

        self.assertEqual(get.call_count, 2)

    @patch("app.requests.get")
    def test_failed_lookup_is_not_cached(self, get):
        get.side_effect = [
            RuntimeError("network down"),
            FakeResponse({"entities": {"P31": {"labels": {"en": {"value": "instance of"}}}}}),
        ]

        self.assertEqual(app.fetch_labels({"P31"}), {})
        self.assertEqual(app.fetch_labels({"P31"}), {"P31": "instance of"})


class WaybackCacheTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()

    def test_snapshot_is_cached(self):
        session = FakeAiohttpSession(data=SNAPSHOT)

        first = wayback(session, "https://dead.test/")
        second = wayback(session, "https://dead.test/")

        self.assertEqual(session.calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(first["timestamp"], "20200101000000")

    def test_missing_snapshot_is_cached_for_a_shorter_time(self):
        session = FakeAiohttpSession(data={"archived_snapshots": {}})
        clock = FakeClock()
        with patch("app.time.monotonic", clock):
            self.assertIsNone(wayback(session, "https://dead.test/"))
            self.assertIsNone(wayback(session, "https://dead.test/"))
            self.assertEqual(session.calls, 1)

            clock.now += app.WAYBACK_MISS_TTL
            wayback(session, "https://dead.test/")
            self.assertEqual(session.calls, 2)

    def test_failures_are_not_cached(self):
        for session in (
            FakeAiohttpSession(error=RuntimeError("timeout")),
            FakeAiohttpSession(status=503),
            FakeAiohttpSession(data=["unexpected"]),
        ):
            app.clear_caches()
            self.assertIsNone(wayback(session, "https://dead.test/"))
            self.assertIsNone(wayback(session, "https://dead.test/"))
            self.assertEqual(session.calls, 2)

    def test_refresh_bypasses_the_cache(self):
        session = FakeAiohttpSession(data=SNAPSHOT)

        wayback(session, "https://dead.test/")
        wayback(session, "https://dead.test/", refresh=True)

        self.assertEqual(session.calls, 2)


class ValidateRefreshTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_refresh_flag_is_read_from_query_or_body(self):
        cases = [
            ("/validate?refresh=1", {}, True),
            ("/validate", {"refresh": True}, True),
            ("/validate", {"refresh": "1"}, True),
            ("/validate", {}, False),
            ("/validate?refresh=0", {"refresh": True}, False),
        ]
        for path, body, expected in cases:
            with app.app.test_request_context(path, method="POST", json=body):
                self.assertEqual(app.wants_refresh(), expected, (path, body))

    @patch("app.fetch_labels", return_value={})
    @patch("app.requests.get")
    def test_validate_passes_refresh_to_label_lookup(self, get, fetch_labels):
        claims = {"P31": [{
            "mainsnak": {},
            "references": [{"snaks": {"P854": [{"datavalue": {"value": "https://example.test"}}]}}],
        }]}
        get.return_value = FakeResponse({"entities": {"Q42": {"claims": claims}}})

        def finish_checks(coroutine):
            coroutine.close()
            return {"https://example.test": {"status": "alive", "httpStatus": 200}}, {}

        with patch("app.asyncio.run", side_effect=finish_checks):
            self.client.post("/validate?refresh=1", json={"qid": "Q42"})
            self.client.post("/validate", json={"qid": "Q42"})

        self.assertTrue(fetch_labels.call_args_list[0].args[2])
        self.assertFalse(fetch_labels.call_args_list[1].args[2])


if __name__ == "__main__":
    unittest.main()

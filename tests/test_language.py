"""Tests for display-language selection (search + label lookups)."""

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


def complete_url_checks(coroutine):
    """Stand in for asyncio.run(), which would otherwise hit the live web."""
    coroutine.close()
    return (
        {"https://example.test": {"url": "https://example.test", "status": "alive", "httpStatus": 200}},
        {},
    )


class LanguageHelpersTests(unittest.TestCase):
    def test_unsupported_or_missing_language_falls_back_to_english(self):
        for value in (None, "", "xx", "EN-gb", 42):
            self.assertEqual(app.normalize_language(value), "en")

    def test_language_codes_are_normalized(self):
        self.assertEqual(app.normalize_language(" FR "), "fr")

    def test_fallback_chain_ends_with_english_and_mul(self):
        self.assertEqual(app.language_fallbacks("fr"), ["fr", "en", "mul"])
        self.assertEqual(app.language_fallbacks("en"), ["en", "mul"])
        self.assertEqual(app.language_fallbacks("xx"), ["en", "mul"])

    def test_pick_label_prefers_requested_then_english_then_mul(self):
        terms = {
            "fr": {"value": "Pays"},
            "en": {"value": "Country"},
            "mul": {"value": "Staat"},
        }
        self.assertEqual(app.pick_label(terms, "", "fr"), "Pays")
        self.assertEqual(app.pick_label(terms, "", "de"), "Country")
        self.assertEqual(app.pick_label({"mul": {"value": "Neutral"}}, "", "ja"), "Neutral")
        self.assertEqual(app.pick_label({}, "fallback", "fr"), "fallback")


class SearchTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_homepage_offers_every_language_with_english_selected(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="en" selected>English</option>', response.data)
        for code, name in app.SUPPORTED_LANGUAGES:
            self.assertIn(f'<option value="{code}"'.encode(), response.data)
            self.assertIn(name.encode(), response.data)

    @patch("app.requests.get")
    def test_search_uses_requested_language(self, get):
        get.return_value = FakeResponse({
            "search": [{"id": "Q90", "label": "Paris", "description": "capitale de la France"}]
        })

        response = self.client.get("/search?q=Paris&language=fr")

        self.assertEqual(response.status_code, 200)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["language"], "fr")
        self.assertEqual(params["uselang"], "fr")
        self.assertEqual(response.get_json()[0]["label"], "Paris")

    @patch("app.requests.get")
    def test_search_defaults_unknown_language_to_english(self, get):
        get.return_value = FakeResponse({"search": []})

        self.client.get("/search?q=Paris&language=klingon")

        params = get.call_args.kwargs["params"]
        self.assertEqual(params["language"], "en")
        self.assertEqual(params["uselang"], "en")

    @patch("app.requests.get")
    def test_search_fills_missing_labels_from_fallback_chain(self, get):
        get.side_effect = [
            FakeResponse({"search": [{"id": "Q90", "description": "capitale"}]}),
            FakeResponse({"entities": {"Q90": {"labels": {"fr": {"value": "Paris (fr)"}}}}}),
        ]

        response = self.client.get("/search?q=Paris&language=fr")

        self.assertEqual(response.get_json(), [
            {"id": "Q90", "label": "Paris (fr)", "description": "capitale"}
        ])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["languages"], "fr|en|mul")

    @patch("app.requests.get")
    def test_search_reports_upstream_failure_instead_of_crashing(self, get):
        get.side_effect = RuntimeError("network down")

        response = self.client.get("/search?q=Paris")

        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.get_json())

    def test_empty_query_short_circuits(self):
        self.assertEqual(self.client.get("/search?q=").get_json(), [])


class LabelFetchTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()

    @patch("app.requests.get")
    def test_fetch_labels_requests_fallback_chain(self, get):
        get.return_value = FakeResponse({
            "entities": {
                "P17": {"labels": {"fr": {"value": "Pays"}, "en": {"value": "Country"}}},
                "P36": {"labels": {"en": {"value": "Capital"}}},
                "P999": {"labels": {}},
            }
        })

        labels = app.fetch_labels({"P36", "P17", "P999"}, "fr")

        self.assertEqual(labels, {"P17": "Pays", "P36": "Capital"})
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["languages"], "fr|en|mul")
        self.assertEqual(params["ids"], "P17|P36|P999")


class ValidateTests(unittest.TestCase):
    def setUp(self):
        app.clear_caches()
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

    def test_missing_input_is_rejected(self):
        response = self.client.post("/validate", json={})
        self.assertEqual(response.status_code, 400)

    @patch("app.asyncio.run", side_effect=complete_url_checks)
    @patch("app.requests.get")
    def test_validate_uses_selected_language_and_keeps_response_shape(self, get, _run):
        claims = {
            "P17": [{
                "mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q30"}}},
                "references": [{
                    "snaks": {"P854": [{"datavalue": {"value": {"uri": "https://example.test"}}}]}
                }],
            }]
        }
        get.side_effect = [
            FakeResponse({"entities": {"Q42": {
                "labels": {"fr": {"value": "Douglas Adams"}},
                "descriptions": {"fr": {"value": "écrivain anglais"}},
                "claims": claims,
            }}}),
            FakeResponse({"entities": {
                "P17": {"labels": {"fr": {"value": "Pays"}}},
                "Q30": {"labels": {"fr": {"value": "États-Unis"}}},
            }}),
        ]

        response = self.client.post("/validate", json={"qid": "Q42", "language": "fr"})

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        # The redesigned UI reads all of these fields.
        self.assertEqual(body["qid"], "Q42")
        self.assertEqual(body["label"], "Douglas Adams")
        self.assertEqual(body["description"], "écrivain anglais")
        self.assertEqual(body["language"], "fr")
        self.assertEqual(len(body["references"]), 1)
        self.assertEqual(body["references"][0]["propertyLabel"], "Pays")
        self.assertEqual(body["references"][0]["statementValue"], "États-Unis")
        self.assertEqual(get.call_args_list[1].kwargs["params"]["languages"], "fr|en|mul")

    @patch("app.asyncio.run", side_effect=complete_url_checks)
    @patch("app.requests.get")
    def test_validate_defaults_to_english(self, get, _run):
        get.side_effect = [
            FakeResponse({"entities": {"Q42": {"claims": {}}}}),
        ]

        response = self.client.post("/validate", json={"qid": "Q42"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["language"], "en")
        self.assertEqual(response.get_json()["qid"], "Q42")


if __name__ == "__main__":
    unittest.main()

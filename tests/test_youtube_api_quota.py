import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from skimmer.collectors import youtube_api
from skimmer.storage.bronze import get_youtube_api_quota_usage, initialize_database


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class EndpointCostTests(unittest.TestCase):
    def test_search_is_charged_one_hundred_units(self):
        self.assertEqual(youtube_api.endpoint_cost("search"), 100)

    def test_list_endpoints_are_charged_one_unit(self):
        for endpoint in ("channels", "playlistItems", "videos"):
            self.assertEqual(youtube_api.endpoint_cost(endpoint), 1)

    def test_unknown_endpoint_raises_rather_than_defaulting(self):
        with self.assertRaises(ValueError):
            youtube_api.endpoint_cost("subscriptions")


class RequestQuotaTests(unittest.TestCase):
    def setUp(self):
        handle, self.database_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.database_path)
        initialize_database(self.database_path)
        patcher = patch.object(youtube_api, "youtube_api_key", return_value="key")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if os.path.exists(self.database_path):
            os.unlink(self.database_path)

    def _usage(self):
        return get_youtube_api_quota_usage(database_path=self.database_path)

    @staticmethod
    def _ok(*_args, **_kwargs):
        # A fresh response per call; _request_json closes it via the context
        # manager, so a shared instance would fail on the second request.
        return _Response(json.dumps({"items": []}).encode("utf-8"))

    def test_search_request_reserves_full_cost(self):
        with patch("urllib.request.urlopen", side_effect=self._ok):
            youtube_api._request_json(
                "search", {"q": "x"}, database_path=self.database_path, budget=1000
            )
        self.assertEqual(self._usage(), 100)

    def test_budget_stops_search_before_real_quota_is_exhausted(self):
        with patch("urllib.request.urlopen", side_effect=self._ok):
            for _ in range(2):
                youtube_api._request_json(
                    "search", {"q": "x"}, database_path=self.database_path, budget=250
                )
            with self.assertRaises(youtube_api.QuotaExceeded):
                youtube_api._request_json(
                    "search", {"q": "x"}, database_path=self.database_path, budget=250
                )
        self.assertEqual(self._usage(), 200)

    def test_transport_failure_refunds_the_reservation(self):
        error = urllib.error.URLError("connection reset")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError):
                youtube_api._request_json(
                    "search", {"q": "x"}, database_path=self.database_path, budget=1000
                )
        self.assertEqual(self._usage(), 0)

    def test_http_error_keeps_the_reservation(self):
        # The API answered, so real quota was spent even though the call failed.
        error = urllib.error.HTTPError(
            "https://example.test", 404, "Not Found", {}, io.BytesIO(b"{}")
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError):
                youtube_api._request_json(
                    "search", {"q": "x"}, database_path=self.database_path, budget=1000
                )
        self.assertEqual(self._usage(), 100)


if __name__ == "__main__":
    unittest.main()

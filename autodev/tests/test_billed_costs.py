"""OpenRouter billed costs, settled after the fact (autodev/pipeline/billed_costs.py).

OpenClaw prices each response at the model's list price and swaps in
OpenRouter's billed cost only when its immediate generation lookup succeeds,
which it does not: OpenRouter publishes the record a minute or two later. These
pin the classification the orchestrator records on each metrics row and the
settlement the dashboard applies once the billed cost is known.
"""
import http.server
import io
import json
import threading
import urllib.error

import pytest

from autodev.pipeline import billed_costs as bc


def _message(total, parts=None, provider="openrouter", response_id="gen-1-abc"):
    cost = {"total": total}
    if parts is not None:
        cost.update(dict(zip(("input", "output", "cacheRead", "cacheWrite"), parts)))
    return {
        "role": "assistant",
        "provider": provider,
        "responseId": response_id,
        "usage": {"input": 10, "output": 2, "cost": cost},
    }


class TestClassify:
    def test_list_priced_openrouter_response_is_pending(self):
        # The total is OpenClaw's own sum of the parts: its lookup failed.
        parts = (0.0025113, 0.0002784, 0.0, 0.0)
        total = parts[0] + parts[1] + parts[2] + parts[3]
        assert bc.classify_message_cost(_message(total, parts)) == (total, True, "gen-1-abc")

    def test_total_that_differs_from_its_parts_is_billed(self):
        msg = _message(0.000408985, (0.0025113, 0.0002784, 0.0, 0.0))
        assert bc.classify_message_cost(msg) == (0.000408985, False, None)

    def test_other_providers_are_estimates_without_a_lookup_id(self):
        msg = _message(0.02, (0.015, 0.005, 0.0, 0.0), provider="anthropic", response_id="msg_1")
        assert bc.classify_message_cost(msg) == (0.02, True, None)

    def test_openrouter_response_without_a_generation_id_stays_estimated(self):
        msg = _message(0.01, (0.01, 0.0, 0.0, 0.0), response_id="chatcmpl-1")
        assert bc.classify_message_cost(msg) == (0.01, True, None)

    def test_total_without_its_parts_is_not_read_as_billed(self):
        # Without the parts there is no evidence OpenClaw replaced the total.
        assert bc.classify_message_cost(_message(0.01)) == (0.01, True, "gen-1-abc")

    @pytest.mark.parametrize(
        "message",
        [
            {},
            None,
            "x",
            {"usage": None},
            {"usage": {"cost": None}},
            _message(0),
            _message(0.0, (0, 0, 0, 0)),
            _message(float("nan")),
            _message(True),
        ],
    )
    def test_free_or_unpriced_messages_are_not_estimates(self, message):
        assert bc.classify_message_cost(message) == (0.0, False, None)


class TestSettle:
    def _tokens(self):
        return {
            "input": 100,
            "output": 10,
            "total_tokens": 110,
            "cost_total": 0.3,
            "cost_estimated": 0.25,
            "cost_pending": {"gen-a": 0.1, "gen-b": 0.15},
        }

    def test_billed_cost_replaces_its_estimate(self):
        tok = self._tokens()
        out = bc.settle_tokens(tok, {"gen-a": 0.02})
        assert out["cost_total"] == pytest.approx(0.3 - 0.1 + 0.02)
        assert out["cost_estimated"] == pytest.approx(0.15)
        assert out["cost_pending"] == {"gen-b": 0.15}
        assert out["total_tokens"] == 110
        assert tok["cost_pending"] == {"gen-a": 0.1, "gen-b": 0.15}  # not mutated

    def test_fully_settled_role_reads_zero_estimate_and_drops_pending(self):
        tok = {"cost_total": 0.3, "cost_estimated": 0.3, "cost_pending": {"gen-a": 0.1, "gen-b": 0.2}}
        out = bc.settle_tokens(tok, {"gen-a": 0.01, "gen-b": 0.02})
        assert out["cost_total"] == pytest.approx(0.03)
        assert out["cost_estimated"] == 0.0
        assert "cost_pending" not in out

    def test_nothing_to_settle_returns_the_same_object(self):
        tok = self._tokens()
        assert bc.settle_tokens(tok, {"gen-z": 1.0}) is tok
        assert bc.settle_tokens(tok, {}) is tok
        plain = {"cost_total": 1.0}
        assert bc.settle_tokens(plain, {"gen-a": 1.0}) is plain

    def test_row_totals_follow_the_settled_roles(self):
        row = {
            "phase": "CORE-E1",
            "planner_tokens": {"cost_total": 0.1, "cost_estimated": 0.1, "cost_pending": {"gen-p": 0.1}},
            "executor_tokens": {"cost_total": 0.5, "cost_estimated": 0.0},
            "reviewer_tokens": {"cost_total": 0.4, "cost_estimated": 0.4, "cost_pending": {"gen-r": 0.4}},
            "cost_total": 1.0,
            "cost_estimated": 0.5,
        }
        out = bc.settle_row(row, {"gen-p": 0.01, "gen-r": 0.05})
        assert out["cost_total"] == pytest.approx(0.56)
        assert out["cost_estimated"] == 0.0
        assert out["executor_tokens"] is row["executor_tokens"]
        assert row["cost_total"] == 1.0  # not mutated
        assert bc.settle_row(row, {"gen-x": 1.0}) is row

    def test_pending_ids_in_first_seen_order(self):
        rows = [
            {
                "planner_tokens": {"cost_pending": {"gen-1": 0.1}},
                "reviewer_tokens": {"cost_pending": {"gen-2": 0.1, "gen-1": 0.1}},
            },
            {"executor_tokens": {"cost_pending": {"gen-3": 0.1}}},
            {"phase": "legacy"},
            "junk",
            None,
        ]
        assert bc.pending_generation_ids(rows) == ["gen-1", "gen-2", "gen-3"]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _opener(payload=None, error=None, seen=None):
    def urlopen(request, timeout):
        if seen is not None:
            seen.append((request.full_url, request.get_header("Authorization"), timeout))
        if error is not None:
            raise error
        return _Response(json.dumps(payload).encode())

    return urlopen


class TestLookup:
    def test_returns_the_billed_total_cost(self):
        seen = []
        opener = _opener({"data": {"total_cost": 0.000408985}}, seen=seen)
        assert bc.lookup_generation_cost("gen-1 a", "sk-test", urlopen=opener) == 0.000408985
        assert seen == [
            ("https://openrouter.ai/api/v1/generation?id=gen-1+a", "Bearer sk-test", 5.0)
        ]

    def test_unpublished_generation_is_none(self):
        error = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        assert bc.lookup_generation_cost("gen-1", "k", urlopen=_opener(error=error)) is None

    @pytest.mark.parametrize(
        "error", [urllib.error.URLError("down"), TimeoutError(), OSError("reset")]
    )
    def test_network_errors_are_none(self, error):
        assert bc.lookup_generation_cost("gen-1", "k", urlopen=_opener(error=error)) is None

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"data": None},
            {"data": {}},
            {"data": {"total_cost": None}},
            {"data": {"total_cost": "0.1"}},
            {"data": {"total_cost": -1}},
            {"data": {"total_cost": True}},
            [],
            "x",
        ],
    )
    def test_unusable_payloads_are_none(self, payload):
        assert bc.lookup_generation_cost("gen-1", "k", urlopen=_opener(payload)) is None

    def test_batch_keeps_found_costs_only(self):
        found = bc.lookup_generation_costs(
            ["gen-1", "gen-2", "gen-1", None], "k", lookup=lambda g, k: {"gen-1": 0.5}.get(g)
        )
        assert found == {"gen-1": 0.5}

    def test_the_key_never_follows_a_redirect(self, monkeypatch):
        """urllib's default opener re-sends every request header, Authorization
        included, to wherever a redirect points, on any host and over any
        scheme. The lookup carries the user's OpenRouter key, so a redirect
        must end it instead (independent review, 2026-11-03)."""
        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data": {"total_cost": 1.0}}')

            def log_message(self, *args):
                pass

        target = http.server.HTTPServer(("127.0.0.1", 0), Target)

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{target.server_port}/elsewhere"
                )
                self.end_headers()

            def log_message(self, *args):
                pass

        origin = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        for server in (target, origin):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("no_proxy", "127.0.0.1")
        monkeypatch.setattr(
            bc, "GENERATION_URL", f"http://127.0.0.1:{origin.server_port}/api/v1/generation"
        )
        try:
            assert bc.lookup_generation_cost("gen-1", "sk-secret") is None
        finally:
            origin.shutdown()
            target.shutdown()
            origin.server_close()
            target.server_close()
        assert seen == [], "the redirect target received the request (and the key)"

    def test_batch_needs_a_key_and_honours_the_limit(self):
        calls = []

        def lookup(gid, key):
            calls.append(gid)
            return 1.0

        assert bc.lookup_generation_costs(["gen-1"], "", lookup=lookup) == {}
        assert calls == []
        found = bc.lookup_generation_costs([f"gen-{i}" for i in range(5)], "k", lookup=lookup, limit=3)
        assert sorted(found) == ["gen-0", "gen-1", "gen-2"]

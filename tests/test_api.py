"""Tests for the Vercel HTTP entry point in api/index.py.

Focus: routing survives Vercel's rewrite path behavior (original path OR the
rewrite destination with the original appended), and Slack retries are deduped.
The Slack handler is mocked, so no real signature verification runs here.
"""
from unittest.mock import patch

import pytest

import api.index as idx


@pytest.fixture
def client():
    return idx.app.test_client()


# --- routing works for both path shapes ------------------------------------

@pytest.mark.parametrize("path", ["/", "/api/index/"])
def test_health(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.data == b"PR Raiser is running."


@pytest.mark.parametrize("path", ["/debug", "/api/index/debug"])
def test_debug_reports_observed_path(client, path):
    j = client.get(path).get_json()
    assert j["observed_path"] == path
    assert j["init_ok"] is True
    assert "GITHUB_TOKEN" in j["env"]


@pytest.mark.parametrize("path", ["/slack/events", "/api/index/slack/events"])
def test_slack_events_reaches_handler(client, path):
    with patch.object(idx, "slack_request_handler") as h:
        h.handle.return_value = "HANDLED"
        r = client.post(path, json={"type": "url_verification", "challenge": "x"})
    assert r.data == b"HANDLED"
    h.handle.assert_called_once()


def test_non_slack_post_does_not_reach_handler(client):
    with patch.object(idx, "slack_request_handler") as h:
        r = client.post("/api/index/", json={"anything": True})
    h.handle.assert_not_called()
    assert r.data == b"PR Raiser is running."


# --- Slack retry idempotency ----------------------------------------------

def test_retry_of_event_callback_is_skipped(client):
    with patch.object(idx, "slack_request_handler") as h:
        r = client.post(
            "/slack/events",
            json={"type": "event_callback", "event": {"type": "message"}},
            headers={"X-Slack-Retry-Num": "1", "X-Slack-Retry-Reason": "http_timeout"},
        )
    assert r.status_code == 200
    h.handle.assert_not_called()  # deduped, not reprocessed


def test_retry_of_url_verification_still_processed(client):
    with patch.object(idx, "slack_request_handler") as h:
        h.handle.return_value = "CHALLENGE"
        r = client.post(
            "/slack/events",
            json={"type": "url_verification", "challenge": "abc"},
            headers={"X-Slack-Retry-Num": "1"},
        )
    assert r.data == b"CHALLENGE"
    h.handle.assert_called_once()


def test_first_delivery_not_skipped(client):
    with patch.object(idx, "slack_request_handler") as h:
        h.handle.return_value = "HANDLED"
        r = client.post(
            "/slack/events",
            json={"type": "event_callback", "event": {"type": "message"}},
        )
    assert r.data == b"HANDLED"
    h.handle.assert_called_once()


# --- init-error surfacing --------------------------------------------------

def test_init_error_surfaces_on_health(client):
    with patch.object(idx, "_init_error", "boom traceback"):
        r = client.get("/")
    assert r.status_code == 500
    assert b"boom traceback" in r.data


def test_init_error_surfaces_on_slack_events(client):
    with patch.object(idx, "_init_error", "boom traceback"):
        r = client.post("/slack/events", json={"type": "event_callback"})
    assert r.status_code == 500
    assert r.get_json()["error"]

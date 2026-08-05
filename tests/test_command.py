"""Tests for the /pr slash command in bot.py."""
from unittest.mock import MagicMock, patch

import pytest

import bot


class FakeResp:
    def __init__(self, status_code=201, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


# --- parse_pr_command ------------------------------------------------------

def test_parse_two_refs():
    p = bot.parse_pr_command("acme/widgets main feature")
    assert (p["owner"], p["repo"], p["base_branch"], p["api_head"]) == \
        ("acme", "widgets", "main", "feature")


def test_parse_compare_style_spec():
    p = bot.parse_pr_command("acme/widgets main...feature")
    assert p["base_branch"] == "main" and p["api_head"] == "feature"


def test_parse_cross_fork():
    p = bot.parse_pr_command("vmockinc/cmc-notes uat someuser:cmc-notes:feat")
    assert p["api_head"] == "someuser:feat" and p["base_branch"] == "uat"


def test_parse_ignores_mentions():
    p = bot.parse_pr_command("acme/widgets main feature <@U123|alice>")
    assert p is not None and p["api_head"] == "feature"


@pytest.mark.parametrize("text", ["", "onlyonetoken", "norepo main feature",
                                  "acme/widgets a b c d"])
def test_parse_malformed_returns_none(text):
    assert bot.parse_pr_command(text) is None


# --- handle_pr_command -----------------------------------------------------

def _cmd(text):
    return {"text": text, "channel_id": "C1", "user_id": "U1"}


def test_command_created_acks_and_responds_in_channel():
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})):
        bot.handle_pr_command(ack, _cmd("acme/widgets main feature"),
                              respond, client=client, context={}, logger=None)
    ack.assert_called_once()
    kw = respond.call_args.kwargs
    assert kw["response_type"] == "in_channel" and "PR #5" in kw["text"]


def test_command_malformed_shows_usage():
    ack, respond = MagicMock(), MagicMock()
    bot.handle_pr_command(ack, _cmd("garbage"), respond)
    ack.assert_called_once()
    # usage hint sent, create_pr never attempted
    assert "Usage" in respond.call_args.args[0] or "Usage" in respond.call_args.kwargs.get("text", "")


def test_command_dms_inline_approver():
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "https://gh/pr/5",
                                                "number": 5, "title": "t"})):
        bot.handle_pr_command(
            ack, _cmd("acme/widgets main feature <@UPERSON>"),
            respond, client=client, context={"bot_user_id": "UBOT"}, logger=None)
    client.chat_postMessage.assert_called_once()
    assert client.chat_postMessage.call_args.kwargs["channel"] == "UPERSON"


def test_command_exists_responds_without_dm():
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("exists", {"html_url": "u", "number": 7})):
        bot.handle_pr_command(ack, _cmd("acme/widgets main feature <@UPERSON>"),
                              respond, client=client, context={}, logger=None)
    assert "already open" in respond.call_args.kwargs["text"]
    client.chat_postMessage.assert_not_called()


@pytest.mark.parametrize("text,expected_head", [
    ("vmockinc/cmc-notes uat someuser:cmc-notes:feat", "someuser:feat"),  # 3-part fork head
    ("vmockinc/cmc-notes uat someuser:feat", "someuser:feat"),            # 2-part fork head
    ("vmockinc/cmc-notes uat...someuser:feat", "someuser:feat"),          # compare-style fork
])
def test_command_cross_fork(text, expected_head):
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})) as cp:
        bot.handle_pr_command(ack, _cmd(text), respond, client=client, context={}, logger=None)
    p = cp.call_args.args[0]
    assert p["owner"] == "vmockinc" and p["repo"] == "cmc-notes"
    assert p["base_branch"] == "uat" and p["api_head"] == expected_head


def test_command_custom_title_body():
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})) as cp:
        bot.handle_pr_command(ack, _cmd("acme/widgets main feat | My Title | My body"),
                              respond, client=client, context={}, logger=None)
    p = cp.call_args.args[0]
    assert p.get("title") == "My Title" and p.get("body") == "My body"


def test_command_title_body_with_approver():
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})) as cp:
        bot.handle_pr_command(ack, _cmd("acme/widgets main feat <@UP> | Title | Body"),
                              respond, client=client, context={"bot_user_id": "UBOT"}, logger=None)
    p = cp.call_args.args[0]
    assert p.get("title") == "Title" and p.get("body") == "Body"
    client.chat_postMessage.assert_called_once()
    assert client.chat_postMessage.call_args.kwargs["channel"] == "UP"


def test_command_approver_after_body_pipes_is_dmed_and_stripped():
    # regression: mention placed at the very end (in the body segment) must
    # still be treated as an approver and removed from the PR body.
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    text = ("vmockinc/dashboard-api-resume-builder master praneatdata:headers "
            "| Added Headers | This is also a test PR for my new feature <@USAKSHAN|Saksham>")
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})) as cp:
        bot.handle_pr_command(ack, _cmd(text), respond,
                              client=client, context={"bot_user_id": "UBOT"}, logger=None)
    p = cp.call_args.args[0]
    assert p["api_head"] == "praneatdata:headers" and p["base_branch"] == "master"
    assert p["title"] == "Added Headers"
    assert p["body"] == "This is also a test PR for my new feature"  # mention stripped
    client.chat_postMessage.assert_called_once()
    assert client.chat_postMessage.call_args.kwargs["channel"] == "USAKSHAN"


def test_command_deploy_note_loops_approver_in():
    # 4th pipe segment is a deploy message; the @mentioned teammate becomes a
    # deploy-watcher carrying it (and still gets the approval DM).
    ack, respond, client = MagicMock(), MagicMock(), MagicMock()
    text = "acme/widgets main feat <@UP> | Title | Body | verify once live"
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})) as cp:
        bot.handle_pr_command(ack, _cmd(text), respond,
                              client=client, context={"bot_user_id": "UBOT"}, logger=None)
    p = cp.call_args.args[0]
    assert p["deploy_note"] == "verify once live" and p["deploy_watchers"] == ["UP"]
    assert client.chat_postMessage.call_args.kwargs["channel"] == "UP"


def test_command_error_reports_detail():
    ack, respond = MagicMock(), MagicMock()
    err = FakeResp(422, {"message": "Validation Failed",
                         "errors": [{"message": "fork_collab"}]})
    with patch.object(bot, "create_pr", return_value=("error", err)):
        bot.handle_pr_command(ack, _cmd("acme/widgets main feature"), respond)
    sent = respond.call_args.kwargs.get("text") or respond.call_args.args[0]
    assert "422" in sent and "fork_collab" in sent

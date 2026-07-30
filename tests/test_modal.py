"""Tests for the /pr guided form (modal)."""
from unittest.mock import MagicMock, patch

import bot


def _text_state(**fields):
    """Build a modal state dict of plain_text_input fields."""
    return {k: {"a": {"type": "plain_text_input", "value": v}} for k, v in fields.items()}


# --- build_pr_modal --------------------------------------------------------

def test_build_pr_modal_structure():
    view = bot.build_pr_modal("C123")
    assert view["type"] == "modal" and view["callback_id"] == "pr_modal"
    assert view["private_metadata"] == "C123"
    blocks = {b["block_id"] for b in view["blocks"]}
    assert {"repo", "base", "head", "title", "body", "approvers"} <= blocks
    approver = next(b for b in view["blocks"] if b["block_id"] == "approvers")
    assert approver["element"]["type"] == "multi_users_select"  # native picker


# --- bare /pr opens the modal ---------------------------------------------

def test_blank_command_opens_modal():
    ack, client = MagicMock(), MagicMock()
    cmd = {"text": "   ", "trigger_id": "T1", "channel_id": "C1", "user_id": "U1"}
    bot.handle_pr_command(ack, cmd, MagicMock(), client=client, context={}, logger=None)
    ack.assert_called_once()
    client.views_open.assert_called_once()
    kw = client.views_open.call_args.kwargs
    assert kw["trigger_id"] == "T1" and kw["view"]["callback_id"] == "pr_modal"
    assert kw["view"]["private_metadata"] == "C1"


def test_command_with_args_does_not_open_modal():
    ack, client = MagicMock(), MagicMock()
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})):
        bot.handle_pr_command(ack, {"text": "acme/widgets main feat", "user_id": "U1"},
                              MagicMock(), client=client, context={}, logger=None)
    client.views_open.assert_not_called()


# --- view_submission -------------------------------------------------------

def _submit(state, private_metadata="C1", user="UREQ", ctx=None):
    ack, client = MagicMock(), MagicMock()
    view = {"state": {"values": state}, "private_metadata": private_metadata}
    body = {"user": {"id": user}}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "https://gh/pr/9",
                                                "number": 9, "title": "t"})) as cp:
        bot.handle_pr_modal_submission(ack, body, view, client=client,
                                       context=ctx or {}, logger=None)
    return ack, client, cp


def _channels(client):
    return [c.kwargs.get("channel") for c in client.chat_postMessage.call_args_list]


def test_modal_submission_creates_pr_with_fields():
    state = _text_state(repo="vmockinc/resume-ui", base="main", head="praneatdata:headers",
                        title="My Title", body="My body")
    ack, client, cp = _submit(state)
    ack.assert_called_once_with()  # plain ack → modal closes
    p = cp.call_args.args[0]
    assert p["owner"] == "vmockinc" and p["repo"] == "resume-ui"
    assert p["api_head"] == "praneatdata:headers" and p["base_branch"] == "main"
    assert p["title"] == "My Title" and p["body"] == "My body"
    assert "C1" in _channels(client)  # result posted to originating channel


def test_modal_submission_dms_selected_approvers_with_requester():
    state = _text_state(repo="acme/widgets", base="main", head="feat")
    state["approvers"] = {"a": {"type": "multi_users_select", "selected_users": ["UP1", "UP2"]}}
    ack, client, cp = _submit(state, user="UREQ", ctx={"bot_user_id": "UBOT"})
    dmed = _channels(client)
    assert "UP1" in dmed and "UP2" in dmed
    dm_texts = [c.kwargs["text"] for c in client.chat_postMessage.call_args_list
                if c.kwargs.get("channel") in ("UP1", "UP2")]
    assert all("<@UREQ>" in t for t in dm_texts)  # requester named


def test_modal_submission_excludes_bot_from_approvers():
    state = _text_state(repo="acme/widgets", base="main", head="feat")
    state["approvers"] = {"a": {"selected_users": ["UBOT", "UP1"]}}
    ack, client, cp = _submit(state, ctx={"bot_user_id": "UBOT"})
    dmed = _channels(client)
    assert "UBOT" not in dmed and "UP1" in dmed


def test_modal_submission_invalid_repo_returns_error():
    state = _text_state(repo="not-a-repo", base="main", head="feat")
    ack, client = MagicMock(), MagicMock()
    view = {"state": {"values": state}, "private_metadata": "C1"}
    bot.handle_pr_modal_submission(ack, {"user": {"id": "U"}}, view, client=client)
    assert ack.call_args.kwargs.get("response_action") == "errors"
    client.chat_postMessage.assert_not_called()

"""Tests for the /pr guided form (modal)."""
from unittest.mock import MagicMock, patch

import pytest

import bot

REPOS = ["resume-ui", "dashboard-ui", "cmc-notes"]


@pytest.fixture(autouse=True)
def _org_repos():
    # build_pr_modal fetches the repo list for the dropdown; keep it offline.
    with patch.object(bot, "list_org_repos", return_value=REPOS):
        yield


def _state(repo="vmockinc/resume-ui", base="main", head="feat", **extra):
    """Modal state: repo as a static_select, everything else plain text."""
    s = {"repo": {"a": {"type": "static_select",
                        "selected_option": {"value": repo,
                                            "text": {"type": "plain_text", "text": repo}}}}}
    for k, v in dict(base=base, head=head, **extra).items():
        s[k] = {"a": {"type": "plain_text_input", "value": v}}
    return s


# --- build_pr_modal --------------------------------------------------------

def test_repo_block_is_a_dropdown_of_org_repos():
    view = bot.build_pr_modal("C123")
    repo = next(b for b in view["blocks"] if b["block_id"] == "repo")
    el = repo["element"]
    assert el["type"] == "static_select"
    values = [o["value"] for o in el["options"]]
    assert "vmockinc/dashboard-ui" in values and "vmockinc/resume-ui" in values
    # labels show the full owner/repo so the base owner is obvious
    labels = [o["text"]["text"] for o in el["options"]]
    assert "vmockinc/dashboard-ui" in labels


def test_repo_block_falls_back_to_text_when_no_repos():
    with patch.object(bot, "list_org_repos", return_value=[]):
        repo = next(b for b in bot.build_pr_modal("C1")["blocks"] if b["block_id"] == "repo")
    assert repo["element"]["type"] == "plain_text_input"
    assert repo["element"]["initial_value"] == "vmockinc/"


def test_modal_has_all_fields():
    blocks = {b["block_id"] for b in bot.build_pr_modal("C1")["blocks"]}
    assert {"repo", "base", "head", "title", "body", "approvers"} <= blocks


# --- bare /pr opens the modal ---------------------------------------------

def test_blank_command_opens_modal():
    ack, client = MagicMock(), MagicMock()
    cmd = {"text": "  ", "trigger_id": "T1", "channel_id": "C1", "user_id": "U1"}
    bot.handle_pr_command(ack, cmd, MagicMock(), client=client, context={}, logger=None)
    client.views_open.assert_called_once()
    kw = client.views_open.call_args.kwargs
    assert kw["trigger_id"] == "T1" and kw["view"]["callback_id"] == "pr_modal"


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
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "https://gh/pr/9",
                                                "number": 9, "title": "t"})) as cp:
        bot.handle_pr_modal_submission(ack, {"user": {"id": user}}, view,
                                       client=client, context=ctx or {}, logger=None)
    return ack, client, cp


def _channels(client):
    return [c.kwargs.get("channel") for c in client.chat_postMessage.call_args_list]


def test_submission_uses_dropdown_repo():
    state = _state(repo="vmockinc/dashboard-ui", base="uat", head="someuser:feat",
                   title="My Title", body="My body")
    ack, client, cp = _submit(state)
    ack.assert_called_once_with()
    p = cp.call_args.args[0]
    assert p["owner"] == "vmockinc" and p["repo"] == "dashboard-ui"
    assert p["base_branch"] == "uat" and p["api_head"] == "someuser:feat"
    assert p["title"] == "My Title" and p["body"] == "My body"
    assert "C1" in _channels(client)


def test_submission_dms_selected_approvers_with_requester():
    state = _state()
    state["approvers"] = {"a": {"type": "multi_users_select", "selected_users": ["UP1", "UP2"]}}
    ack, client, cp = _submit(state, user="UREQ", ctx={"bot_user_id": "UBOT"})
    dmed = _channels(client)
    assert "UP1" in dmed and "UP2" in dmed
    dm_texts = [c.kwargs["text"] for c in client.chat_postMessage.call_args_list
                if c.kwargs.get("channel") in ("UP1", "UP2")]
    assert all("<@UREQ>" in t for t in dm_texts)


def test_submission_excludes_bot_from_approvers():
    state = _state()
    state["approvers"] = {"a": {"selected_users": ["UBOT", "UP1"]}}
    ack, client, cp = _submit(state, ctx={"bot_user_id": "UBOT"})
    dmed = _channels(client)
    assert "UBOT" not in dmed and "UP1" in dmed


def test_submission_invalid_repo_returns_error():
    state = _state(repo="not-a-repo")
    ack, client = MagicMock(), MagicMock()
    view = {"state": {"values": state}, "private_metadata": "C1"}
    bot.handle_pr_modal_submission(ack, {"user": {"id": "U"}}, view, client=client)
    assert ack.call_args.kwargs.get("response_action") == "errors"
    client.chat_postMessage.assert_not_called()

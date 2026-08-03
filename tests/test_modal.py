"""Tests for the /pr guided form (modal)."""
from unittest.mock import MagicMock, patch

import bot

REPOS = ["resume-ui", "dashboard-ui", "cmc-notes"]


def _state(repo=None, base="main", head="feat", **extra):
    """Modal state: repo is an external_select (selected_option), rest plain text."""
    s = {}
    if repo is not None:
        s["repo"] = {"a": {"type": "external_select",
                           "selected_option": {"value": repo,
                                               "text": {"type": "plain_text", "text": repo}}}}
    for k, v in dict(base=base, head=head, **extra).items():
        s[k] = {"a": {"type": "plain_text_input", "value": v}}
    return s


# --- build_pr_modal --------------------------------------------------------

def test_repo_is_single_external_select():
    blocks = {b["block_id"]: b for b in bot.build_pr_modal("C1")["blocks"]}
    assert "repo_text" not in blocks  # combined into one field
    assert blocks["repo"]["element"]["type"] == "external_select"
    assert blocks["repo"]["element"]["action_id"] == bot.REPO_SELECT_ACTION


def test_modal_has_all_fields():
    blocks = {b["block_id"] for b in bot.build_pr_modal("C1")["blocks"]}
    assert {"repo", "base", "head", "title", "body", "approvers"} <= blocks


# --- repo options handler --------------------------------------------------

def test_repo_options_filters_org_repos():
    ack = MagicMock()
    with patch.object(bot, "list_org_repos", return_value=REPOS):
        bot.handle_repo_options(ack, {"value": "dash"})
    values = [o["value"] for o in ack.call_args.kwargs["options"]]
    assert values == ["vmockinc/dashboard-ui"]  # only the match, shown as full path


def test_repo_options_empty_query_lists_all():
    ack = MagicMock()
    with patch.object(bot, "list_org_repos", return_value=REPOS):
        bot.handle_repo_options(ack, {"value": ""})
    assert len(ack.call_args.kwargs["options"]) == len(REPOS)


def test_repo_options_offers_typed_non_org_repo_verbatim():
    ack = MagicMock()
    with patch.object(bot, "list_org_repos", return_value=REPOS):
        bot.handle_repo_options(ack, {"value": "someorg/their-repo"})
    values = [o["value"] for o in ack.call_args.kwargs["options"]]
    assert values[0] == "someorg/their-repo"  # selectable custom repo


# --- bare /pr opens the modal ---------------------------------------------

def test_blank_command_opens_modal():
    ack, client = MagicMock(), MagicMock()
    bot.handle_pr_command(ack, {"text": "  ", "trigger_id": "T1", "channel_id": "C1", "user_id": "U1"},
                          MagicMock(), client=client, context={}, logger=None)
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

def _submit(state, user="UREQ", ctx=None):
    ack, client = MagicMock(), MagicMock()
    view = {"state": {"values": state}, "private_metadata": "C1"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "https://gh/pr/9",
                                                "number": 9, "title": "t"})) as cp:
        bot.handle_pr_modal_submission(ack, {"user": {"id": user}}, view,
                                       client=client, context=ctx or {}, logger=None)
    return ack, client, cp


def _channels(client):
    return [c.kwargs.get("channel") for c in client.chat_postMessage.call_args_list]


def test_submission_uses_selected_repo():
    ack, client, cp = _submit(_state(repo="vmockinc/dashboard-ui", base="uat", head="x:feat"))
    p = cp.call_args.args[0]
    assert p["owner"] == "vmockinc" and p["repo"] == "dashboard-ui"
    assert p["base_branch"] == "uat" and p["api_head"] == "x:feat"
    assert "C1" in _channels(client)


def test_submission_accepts_non_org_repo():
    # user picked the verbatim option for a repo outside the org
    ack, client, cp = _submit(_state(repo="someorg/their-repo"))
    assert cp.call_args.args[0]["owner"] == "someorg"


def test_submission_no_repo_errors():
    ack, client = MagicMock(), MagicMock()
    view = {"state": {"values": _state()}, "private_metadata": "C1"}  # no repo selection
    bot.handle_pr_modal_submission(ack, {"user": {"id": "U"}}, view, client=client)
    assert ack.call_args.kwargs.get("response_action") == "errors"
    client.chat_postMessage.assert_not_called()


def test_submission_dms_selected_approvers_with_requester():
    state = _state(repo="vmockinc/resume-ui")
    state["approvers"] = {"a": {"type": "multi_users_select", "selected_users": ["UP1", "UP2"]}}
    ack, client, cp = _submit(state, user="UREQ", ctx={"bot_user_id": "UBOT"})
    dmed = _channels(client)
    assert "UP1" in dmed and "UP2" in dmed
    dm_texts = [c.kwargs["text"] for c in client.chat_postMessage.call_args_list
                if c.kwargs.get("channel") in ("UP1", "UP2")]
    assert all("<@UREQ>" in t for t in dm_texts)


def test_submission_excludes_bot_from_approvers():
    state = _state(repo="vmockinc/resume-ui")
    state["approvers"] = {"a": {"selected_users": ["UBOT", "UP1"]}}
    ack, client, cp = _submit(state, ctx={"bot_user_id": "UBOT"})
    dmed = _channels(client)
    assert "UBOT" not in dmed and "UP1" in dmed

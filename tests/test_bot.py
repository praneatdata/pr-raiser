"""Unit tests for the host-agnostic bot logic in bot.py."""
from unittest.mock import MagicMock, patch

import pytest

import bot


class FakeResp:
    """Minimal stand-in for requests.Response."""
    def __init__(self, status_code=201, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json


# --- parse_compare ---------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    # same repo, branch only
    ("main...feature", {"base_branch": "main", "head_owner": "acme",
                        "head_branch": "feature", "api_head": "feature"}),
    # two-dot separator also accepted
    ("main..feature", {"base_branch": "main", "head_owner": "acme",
                       "head_branch": "feature", "api_head": "feature"}),
    # cross-fork, two-part head (owner:branch)
    ("uat...someuser:feat", {"base_branch": "uat", "head_owner": "someuser",
                             "head_branch": "feat", "api_head": "someuser:feat"}),
    # cross-fork, three-part head (owner:repo:branch)
    ("uat...someuser:repo:feat", {"base_branch": "uat", "head_owner": "someuser",
                                  "head_branch": "feat", "api_head": "someuser:feat"}),
    # base carrying an owner prefix -> stripped to the ref
    ("acme:uat...feat", {"base_branch": "uat", "head_owner": "acme",
                         "head_branch": "feat", "api_head": "feat"}),
])
def test_parse_compare(spec, expected):
    p = bot.parse_compare("acme", "widgets", spec)
    for k, v in expected.items():
        assert p[k] == v


def test_parse_compare_no_separator_returns_none():
    assert bot.parse_compare("acme", "widgets", "justabranch") is None


# --- gh_headers ------------------------------------------------------------

def test_gh_headers_default_token(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {})
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_default")
    h = bot.gh_headers({"owner": "acme", "repo": "widgets"})
    assert h["Authorization"] == "Bearer ghp_default"


def test_gh_headers_mapped_token_case_insensitive(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {"acme/widgets": "GH_ACME"})
    monkeypatch.setenv("GH_ACME", "ghp_acme")
    h = bot.gh_headers({"owner": "ACME", "repo": "Widgets"})
    assert h["Authorization"] == "Bearer ghp_acme"


def test_gh_headers_exact_beats_wildcard(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS",
                        {"acme/special": "GH_SPECIAL", "acme/*": "GH_WILD"})
    monkeypatch.setenv("GH_SPECIAL", "ghp_special")
    monkeypatch.setenv("GH_WILD", "ghp_wild")
    assert bot.gh_headers({"owner": "acme", "repo": "special"})["Authorization"] == "Bearer ghp_special"
    assert bot.gh_headers({"owner": "acme", "repo": "other"})["Authorization"] == "Bearer ghp_wild"


def test_gh_headers_missing_env_falls_back(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {"acme/widgets": "GH_MISSING"})
    monkeypatch.delenv("GH_MISSING", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_default")
    h = bot.gh_headers({"owner": "acme", "repo": "widgets"})
    assert h["Authorization"] == "Bearer ghp_default"


# --- create_pr -------------------------------------------------------------

P = {"owner": "acme", "repo": "widgets", "base_branch": "main",
     "head_owner": "acme", "head_branch": "feat", "api_head": "feat"}


def test_create_pr_created(monkeypatch):
    resp = FakeResp(201, {"html_url": "u", "number": 1, "title": "t"})
    with patch.object(bot.requests, "post", return_value=resp) as post:
        status, result = bot.create_pr(dict(P))
    assert status == "created" and result["number"] == 1
    # cross-fork safety: the flag must always be sent as False
    assert post.call_args.kwargs["json"]["maintainer_can_modify"] is False


def test_create_pr_exists(monkeypatch):
    with patch.object(bot.requests, "post", return_value=FakeResp(422)), \
         patch.object(bot, "find_open_pr", return_value={"html_url": "u", "number": 9}):
        status, result = bot.create_pr(dict(P))
    assert status == "exists" and result["number"] == 9


def test_create_pr_error(monkeypatch):
    with patch.object(bot.requests, "post", return_value=FakeResp(422)), \
         patch.object(bot, "find_open_pr", return_value=None):
        status, result = bot.create_pr(dict(P))
    assert status == "error"


# --- handle_message --------------------------------------------------------

def _say():
    return MagicMock()


def test_handle_message_ignores_bots():
    say = _say()
    bot.handle_message({"bot_id": "B1", "text": "github.com/a/b/compare/x...y"}, say, None)
    say.assert_not_called()


def test_handle_message_no_link():
    say = _say()
    bot.handle_message({"text": "just chatting"}, say, None)
    say.assert_not_called()


def test_handle_message_created_posts_rocket():
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})):
        bot.handle_message(event, say, None)
    msg = say.call_args.kwargs["text"]
    assert ":rocket:" in msg and "PR #5" in msg


def test_handle_message_exists_posts_info():
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("exists", {"html_url": "u", "number": 7})):
        bot.handle_message(event, say, None)
    assert ":information_source:" in say.call_args.kwargs["text"]


def test_handle_message_error_posts_detail():
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    err = FakeResp(422, {"message": "Validation Failed",
                         "errors": [{"message": "fork_collab ..."}]})
    with patch.object(bot, "create_pr", return_value=("error", err)):
        bot.handle_message(event, say, None)
    msg = say.call_args.kwargs["text"]
    assert ":x:" in msg and "422" in msg and "fork_collab" in msg


def test_handle_message_uses_thread_ts_when_present():
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat",
             "ts": "1.2", "thread_ts": "0.9"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})):
        bot.handle_message(event, say, None)
    assert say.call_args.kwargs["thread_ts"] == "0.9"


# --- approver mentions -----------------------------------------------------

def test_approver_mentions_none(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    assert bot.approver_mentions({"owner": "acme", "repo": "widgets"}) == ""


def test_approver_mentions_exact_case_insensitive(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {"acme/widgets": ["U1", "U2"]})
    assert bot.approver_mentions({"owner": "ACME", "repo": "Widgets"}) == "<@U1> <@U2>"


def test_approver_mentions_wildcard(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {"acme/*": ["U9"]})
    assert bot.approver_mentions({"owner": "acme", "repo": "anything"}) == "<@U9>"


def test_approver_mentions_exact_beats_wildcard(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {"acme/widgets": ["U1"], "acme/*": ["U9"]})
    assert bot.approver_mentions({"owner": "acme", "repo": "widgets"}) == "<@U1>"


def test_handle_message_created_tags_approvers(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {"acme/widgets": ["U1", "U2"]})
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})):
        bot.handle_message(event, say, None)
    msg = say.call_args.kwargs["text"]
    assert "<@U1> <@U2>" in msg and "approve" in msg


def test_handle_message_created_no_approvers_is_plain(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})):
        bot.handle_message(event, say, None)
    assert "<@" not in say.call_args.kwargs["text"]


def test_handle_message_exists_does_not_tag(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {"acme/widgets": ["U1"]})
    say = _say()
    event = {"text": "github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("exists", {"html_url": "u", "number": 7})):
        bot.handle_message(event, say, None)
    assert "<@" not in say.call_args.kwargs["text"]


# --- inline @mention extraction --------------------------------------------

def test_mentioned_user_ids_extracts_and_dedupes():
    text = "hey <@U111> and <@U222> and <@U111> again"
    assert bot.mentioned_user_ids(text) == ["U111", "U222"]


def test_mentioned_user_ids_handles_labelled_form():
    assert bot.mentioned_user_ids("ping <@U111|alice>") == ["U111"]


def test_mentioned_user_ids_excludes_bot():
    text = "<@UBOT> please open <@U222>"
    assert bot.mentioned_user_ids(text, exclude="UBOT") == ["U222"]


def test_mentioned_user_ids_empty():
    assert bot.mentioned_user_ids("no mentions here") == []
    assert bot.mentioned_user_ids(None) == []


# --- dm_approvers ----------------------------------------------------------

PR = {"html_url": "https://gh/pr/3", "number": 3, "title": "acme:feat → main"}


def test_dm_approvers_dms_each_user():
    client = MagicMock()
    bot.dm_approvers(client, ["U1", "U2"], PR)
    assert client.chat_postMessage.call_count == 2
    channels = {c.kwargs["channel"] for c in client.chat_postMessage.call_args_list}
    assert channels == {"U1", "U2"}
    # the DM carries the PR link and an approval ask
    sent = client.chat_postMessage.call_args_list[0].kwargs["text"]
    assert "https://gh/pr/3" in sent and "approve" in sent.lower()


def test_dm_approvers_survives_one_failure():
    client = MagicMock()
    client.chat_postMessage.side_effect = [Exception("no_such_user"), None]
    bot.dm_approvers(client, ["Ubad", "Ugood"], PR)  # must not raise
    assert client.chat_postMessage.call_count == 2


# --- handle_message inline-mention DM flow ---------------------------------

def test_handle_message_dms_inline_mention(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    say, client = _say(), MagicMock()
    event = {
        "text": "<@UBOT> github.com/acme/widgets/compare/main...feat <@UPERSON>",
        "ts": "1.2",
    }
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "https://gh/pr/5",
                                                "number": 5, "title": "t"})):
        bot.handle_message(event, say, client=client,
                           context={"bot_user_id": "UBOT"}, logger=None)
    # thread reply still posted
    say.assert_called_once()
    # the mentioned person (not the bot) is DMed the PR link
    client.chat_postMessage.assert_called_once()
    kw = client.chat_postMessage.call_args.kwargs
    assert kw["channel"] == "UPERSON" and "https://gh/pr/5" in kw["text"]


def test_handle_message_no_dm_when_only_bot_mentioned(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    say, client = _say(), MagicMock()
    event = {"text": "<@UBOT> github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})):
        bot.handle_message(event, say, client=client,
                           context={"bot_user_id": "UBOT"}, logger=None)
    client.chat_postMessage.assert_not_called()


def test_handle_message_no_dm_on_exists(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    say, client = _say(), MagicMock()
    event = {"text": "github.com/acme/widgets/compare/main...feat <@UPERSON>", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("exists", {"html_url": "u", "number": 7})):
        bot.handle_message(event, say, client=client, context={}, logger=None)
    client.chat_postMessage.assert_not_called()


# --- pipe splitting --------------------------------------------------------

def test_split_basic():
    assert bot.split_command_title_body("cmd | Title | Body") == ("cmd", "Title", "Body")


def test_split_no_pipes():
    assert bot.split_command_title_body("just a command") == ("just a command", None, None)


def test_split_empty_title_keeps_body():
    assert bot.split_command_title_body("cmd |  | Body") == ("cmd", None, "Body")


def test_split_protects_mention_pipe():
    assert bot.split_command_title_body("<@U1|alice> go | T") == ("<@U1|alice> go", "T", None)


def test_split_protects_wrapped_url_pipe():
    wrapped = "<https://gh/x|https://gh/x>"
    assert bot.split_command_title_body(f"{wrapped} | T") == (wrapped, "T", None)


# --- custom title / body ---------------------------------------------------

def test_create_pr_custom_title_body():
    resp = FakeResp(201, {"html_url": "u", "number": 1, "title": "t"})
    with patch.object(bot.requests, "post", return_value=resp) as post:
        bot.create_pr(dict(P, title="Custom Title", body="Custom body"))
    j = post.call_args.kwargs["json"]
    assert j["title"] == "Custom Title" and j["body"] == "Custom body"


def test_create_pr_defaults_when_absent():
    resp = FakeResp(201, {"html_url": "u", "number": 1, "title": "t"})
    with patch.object(bot.requests, "post", return_value=resp) as post:
        bot.create_pr(dict(P))
    j = post.call_args.kwargs["json"]
    assert j["title"] == "acme:feat → main" and "automatically" in j["body"]


def test_handle_message_custom_title_body(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    event = {"text": "github.com/acme/widgets/compare/main...feat | My Title | My body", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})) as cp:
        bot.handle_message(event, _say(), None)
    p = cp.call_args.args[0]
    assert p.get("title") == "My Title" and p.get("body") == "My body"


def test_handle_message_incidental_pipe_still_opens(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    # link is NOT in the leading part → pipes ignored, PR still opens, no custom title
    event = {"text": "fyi | check github.com/acme/widgets/compare/main...feat", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})) as cp:
        bot.handle_message(event, _say(), None)
    p = cp.call_args.args[0]
    assert "title" not in p and "body" not in p


def test_handle_message_wrapped_url_with_title(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    wrapped = ("<https://github.com/acme/widgets/compare/main...feat"
               "|github.com/acme/widgets/compare/main...feat>")
    event = {"text": f"{wrapped} | Real Title", "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})) as cp:
        bot.handle_message(event, _say(), None)
    p = cp.call_args.args[0]
    # the URL's internal pipe was NOT treated as a delimiter
    assert p["owner"] == "acme" and p.get("title") == "Real Title"

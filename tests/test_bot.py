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

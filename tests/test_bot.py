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


# --- org discovery / auto-derived token map --------------------------------

def _fake_org(views):
    """views: {token_env: [repo names visible to it]} -> a _list_org_repos_with stub."""
    return lambda owner, token_env: views.get(token_env, [])


def test_list_org_repos_unions_across_tokens(monkeypatch):
    # No single token sees the whole org, so the resolver must see the union.
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {"vmockinc/cmc-notes": "GITHUB_TOKEN_SAGNIK"})
    monkeypatch.setenv("GITHUB_TOKEN_SAGNIK", "ghp_sagnik")
    monkeypatch.setattr(bot, "_list_org_repos_with", _fake_org({
        "GITHUB_TOKEN": ["dashboard-ui", "resume-ui"],
        "GITHUB_TOKEN_SAGNIK": ["resume-ui", "jobs-ui", "cmc-notes"],
    }))
    assert sorted(bot.list_org_repos("vmockinc")) == \
        ["cmc-notes", "dashboard-ui", "jobs-ui", "resume-ui"]


def test_discovery_picks_the_token_that_can_see_the_repo(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {})
    monkeypatch.setenv("GITHUB_TOKEN_SAGNIK", "ghp_sagnik")
    monkeypatch.setattr(bot, "_list_org_repos_with", _fake_org({
        "GITHUB_TOKEN": ["dashboard-ui"],
        "GITHUB_TOKEN_SAGNIK": ["jobs-ui"],
    }))
    # jobs-ui is invisible to the default token -> discovery routes it to Sagnik's
    assert bot.gh_headers({"owner": "vmockinc", "repo": "jobs-ui"})["Authorization"] \
        == "Bearer ghp_sagnik"
    # a repo the default token can see keeps using it
    assert bot.gh_headers({"owner": "vmockinc", "repo": "dashboard-ui"})["Authorization"] \
        == "Bearer ghp_default_token"


def test_explicit_repo_tokens_override_discovery(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {"vmockinc/jobs-ui": "GITHUB_TOKEN_OVERRIDE"})
    monkeypatch.setenv("GITHUB_TOKEN_OVERRIDE", "ghp_override")
    monkeypatch.setenv("GITHUB_TOKEN_SAGNIK", "ghp_sagnik")
    monkeypatch.setattr(bot, "_list_org_repos_with", _fake_org({
        "GITHUB_TOKEN_SAGNIK": ["jobs-ui"]}))
    assert bot.gh_headers({"owner": "vmockinc", "repo": "jobs-ui"})["Authorization"] \
        == "Bearer ghp_override"


def test_unknown_repo_falls_back_to_default_token(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {})
    monkeypatch.setattr(bot, "_list_org_repos_with", _fake_org({}))
    assert bot.gh_headers({"owner": "someone", "repo": "nope"})["Authorization"] \
        == "Bearer ghp_default_token"


def test_discovery_is_cached_per_owner(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS", {})
    calls = []
    monkeypatch.setattr(bot, "_list_org_repos_with",
                        lambda owner, env: calls.append(env) or ["dashboard-ui"])
    bot.list_org_repos("vmockinc")
    after_first = len(calls)
    bot.list_org_repos("vmockinc")
    bot.gh_headers({"owner": "vmockinc", "repo": "dashboard-ui"})
    assert after_first == len(bot.token_env_names())  # one listing per token...
    assert len(calls) == after_first                  # ...then served from cache


def test_token_env_names_skips_unset_and_dedupes(monkeypatch):
    monkeypatch.setattr(bot, "TOKEN_ENV_VARS",
                        {"a/b": "GITHUB_TOKEN_SAGNIK", "a/c": "GITHUB_TOKEN_SAGNIK",
                         "a/d": "GITHUB_TOKEN_MISSING"})
    monkeypatch.setenv("GITHUB_TOKEN_SAGNIK", "x")
    monkeypatch.delenv("GITHUB_TOKEN_MISSING", raising=False)
    assert bot.token_env_names() == ["GITHUB_TOKEN", "GITHUB_TOKEN_SAGNIK"]


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


def test_create_pr_embeds_requester_and_noted_watchers(monkeypatch):
    import base64
    p = dict(P, requester="UREQ", deploy_watchers=["UP1", "UREQ"],
             deploy_note="verify SSO once live")
    resp = FakeResp(201, {"html_url": "u", "number": 1, "title": "t"})
    with patch.object(bot.requests, "post", return_value=resp) as post:
        bot.create_pr(p)
    body = post.call_args.kwargs["json"]["body"]
    enc = base64.b64encode("verify SSO once live".encode()).decode()
    assert "<!-- pr-raiser:requester=UREQ -->" in body          # raiser: plain (deduped out of watchers)
    assert f"pr-raiser:requester=UP1|{enc}" in body             # teammate: noted
    assert bot.watcher_notes(body) == {"UREQ": "", "UP1": "verify SSO once live"}


def test_create_pr_note_without_watchers_rides_on_requester(monkeypatch):
    import base64
    p = dict(P, requester="UREQ", deploy_note="ping me on live")
    resp = FakeResp(201, {"html_url": "u", "number": 1, "title": "t"})
    with patch.object(bot.requests, "post", return_value=resp) as post:
        bot.create_pr(p)
    body = post.call_args.kwargs["json"]["body"]
    enc = base64.b64encode("ping me on live".encode()).decode()
    assert f"pr-raiser:requester=UREQ|{enc}" in body


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


# --- multiple compare links in one message --------------------------------

def test_handle_message_opens_a_pr_per_compare_link():
    say = _say()
    text = ("github.com/vmockinc/cmc-calendar-management-system/compare/uat...satyasaibhushan:feat?expand=1 "
            "github.com/vmockinc/jobs-ui/compare/uat...satyasaibhushan:feat?expand=1 "
            "github.com/vmockinc/cmc-accounts-data-sync/compare/uat...satyasaibhushan:feat?expand=1")
    calls = []

    def fake(p):
        calls.append((p["owner"], p["repo"]))
        return ("created", {"html_url": "u", "number": len(calls), "title": "t"})

    with patch.object(bot, "create_pr", side_effect=fake):
        bot.handle_message({"text": text, "ts": "1.2"}, say, None)
    assert calls == [("vmockinc", "cmc-calendar-management-system"),
                     ("vmockinc", "jobs-ui"),
                     ("vmockinc", "cmc-accounts-data-sync")]
    say.assert_called_once()
    msg = say.call_args.kwargs["text"]
    assert msg.count(":rocket:") == 3 and "PR #1" in msg and "PR #3" in msg


def _slack_link(url):
    """Slack delivers a pasted link as <url|label>; the label repeats the URL."""
    return f"<{url}|{url.replace('https://', '')}>"


def test_handle_message_one_pr_per_link_despite_slack_url_label():
    # regression: <url|label> contains the compare URL twice, which made the bot
    # open (and report) the same PR twice.
    say = _say()
    url = ("https://github.com/vmockinc/jobs-ui/compare/"
           "uat...satyasaibhushan:user-event-sync-preferences-uat?expand=1")
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 1, "title": "t"})) as cp:
        bot.handle_message({"text": _slack_link(url), "ts": "1"}, say, None)
    assert cp.call_count == 1
    assert say.call_args.kwargs["text"].count(":rocket:") == 1


def test_handle_message_multi_slack_links_dedupe_each():
    say = _say()
    urls = [f"https://github.com/vmockinc/{r}/compare/uat...someone:feat?expand=1"
            for r in ("jobs-ui", "dashboard-ui", "cmc-notes")]
    text = " ".join(_slack_link(u) for u in urls)
    seen = []

    def fake(p):
        seen.append(p["repo"])
        return ("created", {"html_url": "u", "number": len(seen), "title": "t"})

    with patch.object(bot, "create_pr", side_effect=fake):
        bot.handle_message({"text": text, "ts": "1"}, say, None)
    assert seen == ["jobs-ui", "dashboard-ui", "cmc-notes"]  # each exactly once


def test_handle_message_multi_reports_each_outcome():
    say = _say()
    text = ("github.com/a/one/compare/main...f github.com/a/two/compare/main...f "
            "github.com/a/three/compare/main...f")
    outcomes = [("created", {"html_url": "u", "number": 1, "title": "t"}),
                ("exists", {"html_url": "u", "number": 2}),
                ("error", FakeResp(422, {"message": "Validation Failed",
                                         "errors": [{"message": "fork_collab"}]}))]
    with patch.object(bot, "create_pr", side_effect=outcomes):
        bot.handle_message({"text": text, "ts": "1"}, say, None)
    msg = say.call_args.kwargs["text"]
    assert ":rocket:" in msg and ":information_source:" in msg and ":x:" in msg and "fork_collab" in msg


def test_handle_message_multi_shares_title_body():
    say = _say()
    text = ("github.com/a/one/compare/main...f github.com/a/two/compare/main...f "
            "| My Title | My Body")
    seen = []

    def fake(p):
        seen.append((p.get("title"), p.get("body")))
        return ("created", {"html_url": "u", "number": len(seen), "title": "t"})

    with patch.object(bot, "create_pr", side_effect=fake):
        bot.handle_message({"text": text, "ts": "1"}, say, None)
    assert seen == [("My Title", "My Body"), ("My Title", "My Body")]


def test_handle_message_multi_dms_approver_for_each_created():
    client, say = MagicMock(), _say()
    text = "github.com/a/one/compare/main...f github.com/a/two/compare/main...f <@UPERSON>"
    outcomes = [("created", {"html_url": "u1", "number": 1, "title": "t"}),
                ("created", {"html_url": "u2", "number": 2, "title": "t"})]
    with patch.object(bot, "create_pr", side_effect=outcomes):
        bot.handle_message({"text": text, "ts": "1"}, say, client=client,
                           context={"bot_user_id": "UBOT"})
    channels = [c.kwargs.get("channel") for c in client.chat_postMessage.call_args_list]
    assert channels.count("UPERSON") == 2  # one approval DM per newly-opened PR


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


def test_dm_approvers_includes_requester():
    client = MagicMock()
    bot.dm_approvers(client, ["U1"], PR, requester="UREQ")
    sent = client.chat_postMessage.call_args.kwargs["text"]
    assert "<@UREQ>" in sent and "https://gh/pr/3" in sent


def test_dm_approvers_without_requester_is_generic():
    client = MagicMock()
    bot.dm_approvers(client, ["U1"], PR)
    sent = client.chat_postMessage.call_args.kwargs["text"]
    assert "<@" not in sent and "Please review" in sent


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


def test_handle_message_dm_names_requester(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    say, client = _say(), MagicMock()
    event = {"text": "github.com/acme/widgets/compare/main...feat <@UPERSON>",
             "ts": "1.2", "user": "UREQ"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})):
        bot.handle_message(event, say, client=client, context={"bot_user_id": "UBOT"}, logger=None)
    sent = client.chat_postMessage.call_args.kwargs["text"]
    assert "<@UREQ>" in sent  # requester named in the DM


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
    assert "pr-raiser:requester" not in j["body"]  # no marker without a requester


def test_create_pr_embeds_requester_marker():
    resp = FakeResp(201, {"html_url": "u", "number": 1, "title": "t"})
    with patch.object(bot.requests, "post", return_value=resp) as post:
        bot.create_pr(dict(P, requester="UREQ", body="My body"))
    marker_body = post.call_args.kwargs["json"]["body"]
    assert marker_body.startswith("My body")
    assert "<!-- pr-raiser:requester=UREQ -->" in marker_body


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


def test_handle_message_approver_after_body_is_dmed_and_stripped(monkeypatch):
    monkeypatch.setattr(bot, "APPROVERS", {})
    say, client = _say(), MagicMock()
    event = {"text": "github.com/acme/widgets/compare/main...feat | Title | ping <@UP|p>",
             "ts": "1.2"}
    with patch.object(bot, "create_pr",
                      return_value=("created", {"html_url": "u", "number": 5, "title": "t"})) as cp:
        bot.handle_message(event, say, client=client, context={"bot_user_id": "UBOT"}, logger=None)
    p = cp.call_args.args[0]
    assert p.get("body") == "ping"  # mention stripped from body
    client.chat_postMessage.assert_called_once()
    assert client.chat_postMessage.call_args.kwargs["channel"] == "UP"


def test_strip_mentions():
    assert bot._strip_mentions("hello <@U1|x> world") == "hello world"
    assert bot._strip_mentions("<@U1>") == ""


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


def test_404_says_access_not_just_not_found():
    resp = FakeResp(404, {"message": "Not Found"})
    resp.url = "https://api.github.com/repos/vmockinc/shibboleth-sp/pulls"
    text = bot._pr_result_text("error", resp)
    assert "vmockinc/shibboleth-sp" in text and "access" in text
    assert "404" not in text  # the bare code told nobody anything useful


def test_other_errors_keep_their_detail():
    text = bot._pr_result_text("error", FakeResp(422, {"message": "Validation Failed",
                                                      "errors": [{"message": "fork_collab"}]}))
    assert "422" in text and "fork_collab" in text

"""Tests for the KV-backed watcher/dedup store and its use in bot.py.

The bot calls kv.<fn>() at call time, so patching the kv module's attributes with
an in-memory fake exercises the real bot logic without any network.
"""
from unittest.mock import MagicMock, patch

import pytest

import bot
import kv


class FakeKV:
    """In-memory stand-in for the Upstash hash+set primitives."""
    def __init__(self):
        self.h, self.s = {}, {}

    def kv_available(self):
        return True

    def hset(self, key, field, value, nx=False):
        d = self.h.setdefault(key, {})
        if nx and field in d:
            return 0
        d[field] = value
        return 1

    def hgetall(self, key):
        return dict(self.h.get(key, {}))

    def hincrby(self, key, field, amount=1):
        d = self.h.setdefault(key, {})
        d[field] = int(d.get(field, 0)) + int(amount)
        return d[field]

    def sadd(self, key, *members):
        st = self.s.setdefault(key, set())
        added = sum(1 for m in members if m not in st)
        st.update(members)
        return added

    def srem(self, key, *members):
        st = self.s.get(key, set())
        removed = sum(1 for m in members if m in st)
        st.difference_update(members)
        return removed

    def smembers(self, key):
        return list(self.s.get(key, set()))

    def patched(self):
        return patch.multiple(bot.kv, kv_available=self.kv_available, hset=self.hset,
                              hgetall=self.hgetall, hincrby=self.hincrby, sadd=self.sadd,
                              srem=self.srem, smembers=self.smembers)


class FakeResp:
    def __init__(self, ok=True, status_code=200, json_data=None):
        self.ok, self.status_code, self._json = ok, status_code, json_data or {}

    def json(self):
        return self._json


# --- kv.py primitives ------------------------------------------------------

def test_kv_available_reads_either_env(monkeypatch):
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    assert kv.kv_available() is False
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://x.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "tok")
    assert kv.kv_available() is True


def test_kv_command_shape(monkeypatch):
    monkeypatch.setenv("KV_REST_API_URL", "https://x.upstash.io/")
    monkeypatch.setenv("KV_REST_API_TOKEN", "tok")
    resp = MagicMock(); resp.json.return_value = {"result": "OK"}
    with patch.object(kv.requests, "post", return_value=resp) as post:
        kv.hset("k", "f", "v")
    assert post.call_args.kwargs["json"] == ["HSET", "k", "f", "v"]      # trailing slash trimmed
    assert post.call_args.args[0] == "https://x.upstash.io"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_kv_hgetall_pairs_flat_array(monkeypatch):
    monkeypatch.setenv("KV_REST_API_URL", "https://x"); monkeypatch.setenv("KV_REST_API_TOKEN", "t")
    resp = MagicMock(); resp.json.return_value = {"result": ["U1", "", "U2", "note"]}
    with patch.object(kv.requests, "post", return_value=resp):
        assert kv.hgetall("k") == {"U1": "", "U2": "note"}


# --- kv_add_watchers upgrade/no-downgrade ---------------------------------

def test_kv_add_watchers_upgrades_note_but_never_downgrades():
    fake = FakeKV()
    with fake.patched():
        bot.kv_add_watchers("o", "r", 1, {"U1": "", "U2": "note"})
        assert bot.kv_get_watchers("o", "r", 1) == {"U1": "", "U2": "note"}
        # plain re-add of U2 must NOT wipe its note; U1 gains a note (upgrade)
        written = bot.kv_add_watchers("o", "r", 1, {"U1": "hello", "U2": ""})
        assert bot.kv_get_watchers("o", "r", 1) == {"U1": "hello", "U2": "note"}
        assert written == ["U1"]  # only U1 changed


# --- /track uses KV, no repo write ----------------------------------------

def _track(text, user="UREQ", ctx=None):
    ack, respond = MagicMock(), MagicMock()
    bot.handle_track_command(ack, {"text": text, "user_id": user}, respond, context=ctx or {})
    return respond


def test_track_writes_to_kv_without_patching_pr():
    fake = FakeKV()
    pr = {"number": 42, "html_url": "https://gh/pr/42", "body": ""}
    with fake.patched(), \
         patch.object(bot.requests, "get", return_value=FakeResp(json_data=pr)), \
         patch.object(bot.requests, "patch") as gh_patch:
        respond = _track("vmockinc/dashboard-ui#42 <@UALICE> | verify SSO",
                         ctx={"bot_user_id": "UBOT"})
    gh_patch.assert_not_called()  # KV path never touches the target repo
    assert fake.hgetall(bot._kv_watch_key("vmockinc", "dashboard-ui", "42")) == \
        {"UREQ": "", "UALICE": "verify SSO"}
    assert "Tracking" in respond.call_args.args[0]


def test_track_kv_already_tracking_is_noop():
    fake = FakeKV()
    fake.h[bot._kv_watch_key("vmockinc", "dashboard-ui", "42")] = {"UREQ": ""}
    pr = {"number": 42, "html_url": "u", "body": ""}
    with fake.patched(), patch.object(bot.requests, "get", return_value=FakeResp(json_data=pr)):
        respond = _track("vmockinc/dashboard-ui#42")
    assert "already tracking" in respond.call_args.args[0].lower()


# --- create_pr writes watchers to KV (no body markers) --------------------

def test_create_pr_writes_watchers_to_kv_not_body():
    fake = FakeKV()
    p = {"owner": "acme", "repo": "widgets", "base_branch": "main", "head_owner": "acme",
         "head_branch": "feat", "api_head": "feat", "requester": "UREQ",
         "deploy_watchers": ["UP1"], "deploy_note": "verify once live"}
    resp = FakeResp(json_data={"html_url": "u", "number": 7, "title": "t"})
    resp.status_code = 201
    with fake.patched(), patch.object(bot.requests, "post", return_value=resp) as post:
        status, result = bot.create_pr(p)
    assert status == "created"
    assert "pr-raiser:requester" not in post.call_args.kwargs["json"]["body"]  # no hidden markers
    assert fake.hgetall(bot._kv_watch_key("acme", "widgets", 7)) == \
        {"UREQ": "", "UP1": "verify once live"}


# --- build notification reads/writes KV -----------------------------------

def _build_event(sha="abc1234", pipeline="pipeline-dashboard-ui-uat-Pipeline",
                 stages=":white_check_mark: DeployTo-uat-us", ts="1.5"):
    return {"bot_id": "B1", "ts": ts, "channel": "CB", "text": "",
            "attachments": [{"fields": [
                {"title": pipeline, "value": "SUCCEEDED"},
                {"title": "Stages", "value": stages},
                {"title": "Commit Id", "value": sha}]}]}


def test_build_notif_tags_watchers_from_kv_and_dedups():
    fake = FakeKV()
    fake.h[bot._kv_watch_key("vmockinc", "dashboard-ui", 55)] = {"UREQ": "", "UALICE": "run regression"}
    say = MagicMock()
    with fake.patched(), \
         patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 55, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=""):  # no legacy body markers
        bot.handle_build_notification(_build_event(), say)
        text = say.call_args.kwargs["text"]
        assert "<@UREQ>" in text and "<@UALICE>" in text
        assert "> <@UALICE>: run regression" in text
        # dedup slug was recorded in KV
        assert "deployed-uat" in fake.smembers(bot._kv_notif_key("vmockinc", "dashboard-ui", 55))
        # a second identical build message must NOT re-tag
        say.reset_mock()
        bot.handle_build_notification(_build_event(), say)
        say.assert_not_called()


def test_build_notif_no_double_tag_on_message_edit():
    # regression: BuildBot posts once then EDITS the message, so the same status
    # arrives twice. Only the first invocation may tag.
    fake = FakeKV()
    fake.h[bot._kv_watch_key("vmockinc", "dashboard-ui", 190)] = {"UREQ": ""}
    say = MagicMock()
    with fake.patched(), \
         patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 190, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=""):
        bot.handle_build_notification(_build_event(), say)          # original message
        bot.handle_build_notification(_build_event(), say)          # the edit
    say.assert_called_once()


def test_build_notif_concurrent_invocations_tag_once():
    # The real race: two invocations both read the dedup state before either
    # writes. The claim must be atomic, not check-then-set.
    fake = FakeKV()
    fake.h[bot._kv_watch_key("vmockinc", "dashboard-ui", 190)] = {"UREQ": ""}
    say = MagicMock()
    reads = []
    real_sadd = fake.sadd

    def racy_sadd(key, *members):
        # first caller's claim is interleaved with a second full invocation
        reads.append(key)
        if len(reads) == 1:
            with fake.patched(), \
                 patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
                 patch.object(bot, "find_pr_for_commit",
                              return_value={"number": 190, "html_url": "u"}), \
                 patch.object(bot, "fetch_pr_body", return_value=""):
                bot.handle_build_notification(_build_event(), say)
        return real_sadd(key, *members)

    with fake.patched(), patch.object(bot.kv, "sadd", racy_sadd), \
         patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 190, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=""):
        bot.handle_build_notification(_build_event(), say)
    say.assert_called_once()  # exactly one tag despite overlapping runs


def test_build_notif_releases_claim_when_post_fails():
    # If Slack rejects the message nobody was told, so the status must stay
    # claimable for the next build message.
    fake = FakeKV()
    fake.h[bot._kv_watch_key("vmockinc", "dashboard-ui", 190)] = {"UREQ": ""}
    say = MagicMock(side_effect=RuntimeError("slack down"))
    with fake.patched(), \
         patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 190, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=""):
        with pytest.raises(RuntimeError):
            bot.handle_build_notification(_build_event(), say)
        assert fake.smembers(bot._kv_notif_key("vmockinc", "dashboard-ui", 190)) == []
        # a later build message can still report it
        ok = MagicMock()
        bot.handle_build_notification(_build_event(), ok)
        ok.assert_called_once()


def test_build_notif_kv_and_legacy_body_markers_merge():
    fake = FakeKV()
    fake.h[bot._kv_watch_key("vmockinc", "dashboard-ui", 55)] = {"UALICE": "note"}
    say = MagicMock()
    legacy_body = "desc\n<!-- pr-raiser:requester=UREQ -->"
    with fake.patched(), \
         patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 55, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=legacy_body):
        bot.handle_build_notification(_build_event(), say)
    text = say.call_args.kwargs["text"]
    assert "<@UREQ>" in text and "<@UALICE>" in text  # body (legacy) + KV both tagged

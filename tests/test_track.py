"""Tests for the /track command (track a PR you opened yourself)."""
import base64
from unittest.mock import MagicMock, patch

import bot


def _b64(s):
    return base64.b64encode(s.encode()).decode()


class FakeResp:
    def __init__(self, ok=True, status_code=200, json_data=None):
        self.ok = ok
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


PR = {"number": 42, "html_url": "https://gh/pr/42", "body": "some description"}


def _cmd(text, user="UREQ"):
    return {"text": text, "user_id": user}


def test_track_stamps_requester_marker_via_url():
    ack, respond = MagicMock(), MagicMock()
    get, patch_req = FakeResp(json_data=PR), FakeResp()
    with patch.object(bot.requests, "get", return_value=get), \
         patch.object(bot.requests, "patch", return_value=patch_req) as pr_patch:
        bot.handle_track_command(ack, _cmd("https://github.com/vmockinc/dashboard-ui/pull/42"), respond)
    ack.assert_called_once()
    # PATCHed the PR body to append this user's marker
    new_body = pr_patch.call_args.kwargs["json"]["body"]
    assert "<!-- pr-raiser:requester=UREQ -->" in new_body and new_body.startswith("some description")
    assert "Tracking" in respond.call_args.args[0]


def test_track_accepts_owner_repo_number():
    ack, respond = MagicMock(), MagicMock()
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=PR)), \
         patch.object(bot.requests, "patch", return_value=FakeResp()) as pr_patch:
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#42"), respond)
    assert "requester=UREQ" in pr_patch.call_args.kwargs["json"]["body"]


def test_track_adds_mentioned_watchers():
    ack, respond = MagicMock(), MagicMock()
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=PR)), \
         patch.object(bot.requests, "patch", return_value=FakeResp()) as pr_patch:
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#42 <@UALICE> <@UBOB>"),
                                 respond, context={"bot_user_id": "UBOT"})
    body = pr_patch.call_args.kwargs["json"]["body"]
    for u in ("UREQ", "UALICE", "UBOB"):
        assert f"pr-raiser:requester={u}" in body


def test_track_attaches_message_to_teammate():
    # `/track <PR> @alice | note` -> alice gets a noted marker, caller a plain one.
    ack, respond = MagicMock(), MagicMock()
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=PR)), \
         patch.object(bot.requests, "patch", return_value=FakeResp()) as pr_patch:
        bot.handle_track_command(
            ack, _cmd("vmockinc/dashboard-ui#42 <@UALICE> | please verify SSO"),
            respond, context={"bot_user_id": "UBOT"})
    body = pr_patch.call_args.kwargs["json"]["body"]
    assert f"pr-raiser:requester=UALICE|{_b64('please verify SSO')}" in body
    assert "<!-- pr-raiser:requester=UREQ -->" in body  # caller: plain, no note
    assert "with your message" in respond.call_args.args[0]


def test_track_self_note_when_no_mention():
    ack, respond = MagicMock(), MagicMock()
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=PR)), \
         patch.object(bot.requests, "patch", return_value=FakeResp()) as pr_patch:
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#42 | ping me on live"), respond)
    body = pr_patch.call_args.kwargs["json"]["body"]
    assert f"pr-raiser:requester=UREQ|{_b64('ping me on live')}" in body


def test_track_upgrades_plain_watcher_with_a_note():
    # An already-tracked plain watcher can be given a note by re-running with `| msg`.
    ack, respond = MagicMock(), MagicMock()
    existing = {"number": 42, "html_url": "u", "body": "x\n<!-- pr-raiser:requester=UREQ -->"}
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=existing)), \
         patch.object(bot.requests, "patch", return_value=FakeResp()) as pr_patch:
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#42 | verify once live"), respond)
    body = pr_patch.call_args.kwargs["json"]["body"]
    assert f"pr-raiser:requester=UREQ|{_b64('verify once live')}" in body


def test_track_already_tracking_is_noop():
    ack, respond = MagicMock(), MagicMock()
    body = {"number": 42, "html_url": "u", "body": "x\n<!-- pr-raiser:requester=UREQ -->"}
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=body)), \
         patch.object(bot.requests, "patch") as pr_patch:
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#42"), respond)
    pr_patch.assert_not_called()
    assert "already tracking" in respond.call_args.args[0].lower()


def test_track_pr_not_found():
    ack, respond = MagicMock(), MagicMock()
    with patch.object(bot.requests, "get", return_value=FakeResp(ok=False, status_code=404)), \
         patch.object(bot.requests, "patch") as pr_patch:
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#999"), respond)
    pr_patch.assert_not_called()
    assert ":x:" in respond.call_args.args[0]


def test_track_no_write_access():
    ack, respond = MagicMock(), MagicMock()
    with patch.object(bot.requests, "get", return_value=FakeResp(json_data=PR)), \
         patch.object(bot.requests, "patch", return_value=FakeResp(ok=False, status_code=403)):
        bot.handle_track_command(ack, _cmd("vmockinc/dashboard-ui#42"), respond)
    assert "write access" in respond.call_args.args[0]


def test_track_malformed_shows_usage():
    ack, respond = MagicMock(), MagicMock()
    bot.handle_track_command(ack, _cmd("nonsense"), respond)
    assert "Usage" in respond.call_args.args[0]


def test_build_notif_tags_all_tracked_requesters():
    say = MagicMock()
    event = {"bot_id": "B1", "ts": "1.5", "text": "",
             "attachments": [{"fields": [
                 {"title": "pipeline-dashboard-ui-uat-Pipeline", "value": "SUCCEEDED"},
                 {"title": "Stages", "value": ":white_check_mark: DeployTo-uat-us"},
                 {"title": "Commit Id", "value": "abc1234"}]}]}
    body = "desc\n<!-- pr-raiser:requester=U1 -->\n<!-- pr-raiser:requester=U2 -->"
    with patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 1, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=body), \
         patch.object(bot, "mark_pr_notified"):
        bot.handle_build_notification(event, say)
    text = say.call_args.kwargs["text"]
    assert "<@U1>" in text and "<@U2>" in text

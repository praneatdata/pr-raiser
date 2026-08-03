"""Tests for tagging the PR requester on a build-completion message."""
from unittest.mock import MagicMock, patch

import bot


def _build_event(url, text="pipeline ... SUCCEEDED", ts="1.23", bot_id="B1"):
    """A bot message with a 'View Commit' button, like BuildBot posts."""
    return {
        "bot_id": bot_id, "ts": ts, "channel": "CBUILDS", "text": text,
        "attachments": [{"actions": [
            {"type": "button", "text": "View Commit", "url": url}]}],
    }


COMMIT_URL = "https://github.com/vmockinc/resume-builder-ui/commit/24a54c0abc123def"


# --- commit URL extraction -------------------------------------------------

def test_commit_url_regex_extracts_parts():
    m = bot.COMMIT_URL_RE.search(COMMIT_URL)
    assert m.groups() == ("vmockinc", "resume-builder-ui", "24a54c0abc123def")


def test_compare_url_is_not_a_commit_url():
    assert bot.COMMIT_URL_RE.search("github.com/a/b/compare/main...feat") is None


# --- handle_build_notification ---------------------------------------------

def test_build_notif_tags_requester_in_thread():
    say = MagicMock()
    event = _build_event(COMMIT_URL)
    pr = {"number": 42, "html_url": "https://gh/pr/42", "body": "<!-- pr-raiser:requester=UREQ -->"}
    with patch.object(bot, "find_pr_for_commit", return_value=pr), \
         patch.object(bot, "pr_requester", return_value="UREQ"):
        bot.handle_build_notification(event, say)
    kw = say.call_args.kwargs
    assert kw["thread_ts"] == "1.23" and "<@UREQ>" in kw["text"] and "#42" in kw["text"]


def test_build_notif_no_reply_without_marker():
    say = MagicMock()
    with patch.object(bot, "find_pr_for_commit", return_value={"number": 1, "html_url": "u"}), \
         patch.object(bot, "pr_requester", return_value=None):
        bot.handle_build_notification(_build_event(COMMIT_URL), say)
    say.assert_not_called()


def test_build_notif_skips_non_success():
    say = MagicMock()
    with patch.object(bot, "find_pr_for_commit") as f:
        bot.handle_build_notification(_build_event(COMMIT_URL, text="pipeline STARTED"), say)
    f.assert_not_called()  # no lookup at all for non-completions
    say.assert_not_called()


def test_build_notif_ignores_message_without_commit_url():
    say = MagicMock()
    with patch.object(bot, "find_pr_for_commit") as f:
        bot.handle_build_notification(_build_event("https://example.com/nope"), say)
    f.assert_not_called()
    say.assert_not_called()


def test_build_notif_no_pr_found():
    say = MagicMock()
    with patch.object(bot, "find_pr_for_commit", return_value=None):
        bot.handle_build_notification(_build_event(COMMIT_URL), say)
    say.assert_not_called()


# --- routing: bot messages go to the build path, not PR creation -----------

def test_bot_message_routes_to_build_notif_not_pr_creation():
    say = MagicMock()
    event = _build_event(COMMIT_URL)
    with patch.object(bot, "create_pr") as cp, \
         patch.object(bot, "handle_build_notification") as hbn:
        bot.handle_message(event, say, client=MagicMock(), context={}, logger=None)
    hbn.assert_called_once()
    cp.assert_not_called()


def test_edit_and_delete_subtypes_ignored():
    say = MagicMock()
    with patch.object(bot, "handle_build_notification") as hbn, \
         patch.object(bot, "create_pr") as cp:
        bot.handle_message({"subtype": "message_changed", "message": {}}, say)
        bot.handle_message({"subtype": "message_deleted"}, say)
    hbn.assert_not_called()
    cp.assert_not_called()

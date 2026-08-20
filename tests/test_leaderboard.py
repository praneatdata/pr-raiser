"""Tests for the monthly leaderboard and its cron entry point."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import bot
import leaderboard
from tests.test_kv import FakeKV


def _at(y, m, d=1, h=9):
    return datetime(y, m, d, h, tzinfo=bot.IST)


# --- month bucketing -------------------------------------------------------

def test_month_key_uses_ist():
    # 31 Aug 23:00 IST is still August, though it is already September in UTC+0
    assert bot.lb_month_key(datetime(2026, 8, 31, 23, 0, tzinfo=bot.IST)) == "prlb:2026-08"


def test_previous_month_from_the_first():
    assert leaderboard.previous_month(_at(2026, 9, 1)) == ("prlb:2026-08", "August 2026")


def test_previous_month_across_new_year():
    assert leaderboard.previous_month(_at(2027, 1, 1)) == ("prlb:2026-12", "December 2026")


# --- counting --------------------------------------------------------------

def test_record_pr_raised_increments_month_and_total():
    fake = FakeKV()
    with fake.patched():
        bot.record_pr_raised("U1", _at(2026, 8, 5))
        bot.record_pr_raised("U1", _at(2026, 8, 6))
        bot.record_pr_raised("U2", _at(2026, 9, 2))
    assert fake.h["prlb:2026-08"] == {"U1": 2}
    assert fake.h["prlb:2026-09"] == {"U2": 1}
    assert fake.h[bot.LB_TOTAL_KEY] == {"U1": 2, "U2": 1}


def test_record_pr_raised_ignores_missing_user():
    fake = FakeKV()
    with fake.patched():
        bot.record_pr_raised(None)
    assert fake.h == {}


def test_counter_failure_never_breaks_pr_creation():
    # a KV outage must not turn a successfully opened PR into an error
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"html_url": "u", "number": 1, "title": "t"}
    p = {"owner": "a", "repo": "b", "base_branch": "main", "head_owner": "a",
         "head_branch": "f", "api_head": "f", "requester": "U1"}
    with patch.object(bot.kv, "kv_available", lambda: True), \
         patch.object(bot.kv, "hincrby", side_effect=bot.requests.RequestException("kv down")), \
         patch.object(bot.requests, "post", return_value=resp):
        assert bot.create_pr(p)[0] == "created"


def test_create_pr_counts_the_requester():
    fake = FakeKV()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"html_url": "u", "number": 1, "title": "t"}
    p = {"owner": "a", "repo": "b", "base_branch": "main", "head_owner": "a",
         "head_branch": "f", "api_head": "f", "requester": "U7"}
    with fake.patched(), patch.object(bot.requests, "post", return_value=resp):
        bot.create_pr(p)
    assert fake.h[bot.LB_TOTAL_KEY] == {"U7": 1}


# --- message ---------------------------------------------------------------

# A month with no frozen baseline, so these exercise the mechanics alone.
MONTH, LABEL = "prlb:2026-10", "October 2026"


def _seed(fake, month=None, total=None):
    fake.h[MONTH] = dict(month or {})
    fake.h[bot.LB_TOTAL_KEY] = dict(total or {})


def test_message_ranks_and_totals():
    fake = FakeKV()
    _seed(fake, {"U1": 12, "U2": 9, "U3": 7}, {"U1": 30, "U2": 20, "U3": 10})
    with fake.patched():
        msg = leaderboard.build_message(MONTH, LABEL)
    assert ":first_place_medal:  <@U1> — *12* PRs" in msg
    assert ":second_place_medal:  <@U2> — *9* PRs" in msg
    all_time = 60 + sum(leaderboard.BASELINE_TOTALS.values())  # counters + frozen baseline
    assert "*28* PRs raised in October" in msg and f"*{all_time}* all-time" in msg


def test_maintainer_is_counted_but_not_ranked():
    fake = FakeKV()
    M = leaderboard.MAINTAINER
    _seed(fake, {M: 5, "U1": 3}, {M: 40, "U1": 3})
    with fake.patched():
        msg = leaderboard.build_message(MONTH, LABEL)
    assert f":first_place_medal:  <@U1>" in msg          # maintainer not on the podium
    assert f":second_place_medal:" not in msg            # and not ranked at all
    assert f"plus *5* from <@{M}>" in msg                # but acknowledged
    assert "*8* PRs raised in October" in msg             # and included in the totals


def test_singular_pr_wording():
    fake = FakeKV()
    _seed(fake, {"U1": 1}, {"U1": 1})
    with fake.patched():
        msg = leaderboard.build_message(MONTH, LABEL)
    assert "<@U1> — *1* PR\n" in msg + "\n" and "*1* PR raised in October" in msg


def test_no_activity_returns_no_message():
    fake = FakeKV()
    with fake.patched():
        assert leaderboard.build_message(MONTH, LABEL) is None


# --- posting ---------------------------------------------------------------

def test_post_monthly_posts_once_per_month():
    fake = FakeKV()
    _seed(fake, {"U1": 4}, {"U1": 4})
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1.1", "channel": "C1"}
    with fake.patched():
        first = leaderboard.post_monthly(client, now=_at(2026, 9, 1))
        second = leaderboard.post_monthly(client, now=_at(2026, 9, 1))
    assert first["status"] == "posted" and second["status"] == "already_posted"
    client.chat_postMessage.assert_called_once()  # a retry must not repeat it


def test_post_monthly_skips_a_quiet_month_and_stays_retryable():
    fake = FakeKV()
    client = MagicMock()
    with fake.patched():
        r = leaderboard.post_monthly(client, now=_at(2026, 11, 1))
        assert r["status"] == "no_activity"
        client.chat_postMessage.assert_not_called()
        # the month wasn't consumed, so a later run can still announce it
        _seed(fake, {"U1": 2}, {"U1": 2})
        client.chat_postMessage.return_value = {"ts": "1.1", "channel": "C1"}
        assert leaderboard.post_monthly(client, now=_at(2026, 11, 1))["status"] == "posted"


def test_post_monthly_reports_august_when_run_on_1_september():
    fake = FakeKV()
    _seed(fake, {"U1": 3}, {"U1": 3})
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1.1", "channel": "C1"}
    with fake.patched():
        leaderboard.post_monthly(client, now=_at(2026, 9, 1))
    assert "August 2026" in client.chat_postMessage.call_args.kwargs["text"]


# --- all-time baseline -----------------------------------------------------

def test_all_time_adds_counters_to_the_frozen_baseline():
    fake = FakeKV()
    with fake.patched():
        fake.h[bot.LB_TOTAL_KEY] = {"U02LJ0Z08KZ": 4, "UNEW": 2}
        totals = leaderboard.all_time_totals()
    assert totals["U02LJ0Z08KZ"] == leaderboard.BASELINE_TOTALS["U02LJ0Z08KZ"] + 4
    assert totals["UNEW"] == 2                                   # someone new to the board
    assert totals["U03RS7FEWD9"] == leaderboard.BASELINE_TOTALS["U03RS7FEWD9"]  # untouched


def test_all_time_figure_includes_the_baseline():
    fake = FakeKV()
    base = sum(leaderboard.BASELINE_TOTALS.values())
    with fake.patched():
        fake.h[MONTH] = {"U1": 3}
        fake.h[bot.LB_TOTAL_KEY] = {"U1": 3}
        msg = leaderboard.build_message(MONTH, LABEL)
    assert f"*{base + 3}* all-time" in msg


def test_month_counts_merge_baseline_and_counters():
    # August is split: the snapshot covers 1-20 Aug, counters everything after.
    fake = FakeKV()
    base = leaderboard.BASELINE_MONTHS["prlb:2026-08"]
    with fake.patched():
        fake.h["prlb:2026-08"] = {"U03S4UWNY9X": 4, "UNEW": 1}
        counts = leaderboard.month_counts("prlb:2026-08")
    assert counts["U03S4UWNY9X"] == base["U03S4UWNY9X"] + 4
    assert counts["UNEW"] == 1
    assert counts["U02LJ0Z08KZ"] == base["U02LJ0Z08KZ"]  # untouched by counters


def test_a_month_with_only_baseline_still_posts():
    fake = FakeKV()
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1.1", "channel": "C1"}
    with fake.patched():  # no counters at all for July
        r = leaderboard.post_monthly(client, now=_at(2026, 8, 1))
    assert r["status"] == "posted"
    text = client.chat_postMessage.call_args.kwargs["text"]
    assert "July 2026" in text and "*46* PRs raised in July" in text


def test_dry_run_never_posts_or_consumes_the_month():
    fake = FakeKV()
    client = MagicMock()
    with fake.patched():
        r = leaderboard.post_monthly(client, now=_at(2026, 8, 1), dry_run=True)
        assert r["status"] == "dry_run" and r["would_post"] is True
        assert "July 2026" in r["preview"]
        client.chat_postMessage.assert_not_called()
        # the month is untouched, so the real run still works
        client.chat_postMessage.return_value = {"ts": "1.1", "channel": "C1"}
        assert leaderboard.post_monthly(client, now=_at(2026, 8, 1))["status"] == "posted"

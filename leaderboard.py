"""
Monthly PR leaderboard, posted to Slack on the 1st.

Counts live in the KV store, incremented by bot.record_pr_raised() whenever a
PR is opened — so this needs no history scan and stays fast however busy the
channel gets (an all-time scan grows every month and would eventually blow the
serverless timeout).

Keys (see bot.lb_month_key / bot.LB_TOTAL_KEY):
  prlb:YYYY-MM  hash  slack_uid -> PRs opened that month
  prlb:total    hash  slack_uid -> PRs opened all time
  prlb:posted   set   month keys already announced (so a retry can't double-post)
"""
import os
from datetime import datetime

import bot
import kv

CHANNEL = os.environ.get("LEADERBOARD_CHANNEL", "C0BHZSMFZB4")
# Counted in the totals, but kept out of the ranking — the bot's own author,
# whose tally is inflated by testing.
MAINTAINER = os.environ.get("LEADERBOARD_MAINTAINER", "U08V0KSE092")
POSTED_KEY = "prlb:posted"
MEDALS = (":first_place_medal:", ":second_place_medal:", ":third_place_medal:")

# PRs raised before the KV counters existed, so the all-time figure doesn't
# restart at zero. Reconstructed on 2026-08-20 from the raising channel's thread
# history plus PR bodies carrying the pr-raiser:requester marker, deduped by PR
# URL. Counters cover everything after that instant, so the two never overlap —
# leave this frozen.
BASELINE_TOTALS = {
    "U08V0KSE092": 31,
    "U02LJ0Z08KZ": 29,
    "U03S4UWNY9X": 25,
    "U02LVM9B25T": 19,
    "U03RKM78ZF0": 15,
    "U02LS4K29KQ": 10,
    "U02LVM9C7DK": 4,
    "U03RS7FEWD9": 3,
}

# Same snapshot, split by month. Without this the first monthly post would cover
# only 20-31 August, since that's when counting began. August's KV counter picks
# up from the snapshot instant, so adding the two gives the whole month.
BASELINE_MONTHS = {
    "prlb:2026-07": {"U02LJ0Z08KZ": 18, "U02LVM9B25T": 9, "U08V0KSE092": 8,
                     "U03RKM78ZF0": 7, "U03RS7FEWD9": 2, "U02LVM9C7DK": 1,
                     "U02LS4K29KQ": 1},
    "prlb:2026-08": {"U03S4UWNY9X": 25, "U08V0KSE092": 23, "U02LJ0Z08KZ": 11,
                     "U02LVM9B25T": 10, "U02LS4K29KQ": 9, "U03RKM78ZF0": 8,
                     "U02LVM9C7DK": 3, "U03RS7FEWD9": 1},
}


def previous_month(now=None):
    """(month_key, "August 2026") for the month before `now` — what a run on the
    1st should report."""
    now = (now or datetime.now(bot.IST)).astimezone(bot.IST)
    last_day = now.replace(day=1) - bot.timedelta(days=1)
    return bot.lb_month_key(last_day), f"{last_day:%B %Y}"


def _counts(key):
    """{uid: count} from a KV hash, ints, biggest first."""
    raw = kv.hgetall(key) or {}
    out = {}
    for uid, n in raw.items():
        try:
            out[uid] = int(n)
        except (TypeError, ValueError):
            continue
    return dict(sorted(out.items(), key=lambda kv_: (-kv_[1], kv_[0])))


def all_time_totals():
    """Per-user totals: the pre-counter baseline plus everything counted since."""
    totals = dict(BASELINE_TOTALS)
    for uid, n in _counts(bot.LB_TOTAL_KEY).items():
        totals[uid] = totals.get(uid, 0) + n
    return totals


def month_counts(month_key):
    """{uid: count} for a month: counters plus any frozen baseline for it."""
    counts = dict(BASELINE_MONTHS.get(month_key, {}))
    for uid, n in _counts(month_key).items():
        counts[uid] = counts.get(uid, 0) + n
    return dict(sorted(counts.items(), key=lambda kv_: (-kv_[1], kv_[0])))


def build_message(month_key, label):
    """The Slack message for a month, or None when nobody raised anything."""
    month = month_counts(month_key)
    totals = all_time_totals()
    if not month:
        return None

    ranked = [(u, n) for u, n in month.items() if u != MAINTAINER]
    rows = [
        f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'}  <@{u}> — *{n}* PR{'s' if n != 1 else ''}"
        for i, (u, n) in enumerate(ranked)
    ]
    lines = [f":trophy: *{label} — PR Raiser leaderboard*"]
    if rows:
        lines.append("Hats off to everyone who shipped this month :raised_hands:\n")
        lines += rows
    else:
        lines.append("_No ranked PRs this month._")

    month_total, all_time = sum(month.values()), sum(totals.values())
    lines.append(f"\n:bar_chart: *{month_total}* PR{'s' if month_total != 1 else ''} "
                 f"raised in {label.split()[0]}  ·  *{all_time}* all-time")
    if MAINTAINER in month:
        lines.append(f"_(plus *{month[MAINTAINER]}* from <@{MAINTAINER}>, "
                     "who stays out of the running.)_")
    lines.append("\n_Paste a compare link in this channel or use `/pr` to raise one — "
                 "several links in one message all count._")
    return "\n".join(lines)


def post_monthly(client, now=None, channel=None, force=False):
    """Post last month's leaderboard. Idempotent: a month is announced once, so
    a cron retry (or a stray request) can't repeat it. Returns a status dict."""
    month_key, label = previous_month(now)
    if not force and kv.sadd(POSTED_KEY, month_key) != 1:
        return {"status": "already_posted", "month": month_key}
    text = build_message(month_key, label)
    if not text:
        kv.srem(POSTED_KEY, month_key)  # nothing to say; let a later run try
        return {"status": "no_activity", "month": month_key}
    resp = client.chat_postMessage(channel=channel or CHANNEL, text=text)
    return {"status": "posted", "month": month_key, "ts": resp["ts"],
            "channel": resp["channel"]}

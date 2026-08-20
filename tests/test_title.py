"""A one-commit PR is titled after that commit, the way GitHub does it."""
from unittest.mock import MagicMock, patch

import bot
# Bound at import time, so conftest's autouse stub doesn't hide the real thing.
from bot import _single_commit_title as single_commit_title

P = {"owner": "acme", "repo": "widgets", "base_branch": "main",
     "head_owner": "acme", "head_branch": "feat", "api_head": "feat"}


def _commit(message, merge=False):
    """A compare-API commit; a merge commit is one with two parents."""
    return {"commit": {"message": message},
            "parents": [{"sha": "a"}, {"sha": "b"}] if merge else [{"sha": "a"}]}


def _compare(total, messages=None, ok=True, commits=None):
    r = MagicMock()
    r.ok = ok
    r.json.return_value = {
        "total_commits": total,
        "commits": commits if commits is not None else [_commit(m) for m in (messages or [])],
    }
    return r


# --- _single_commit_title --------------------------------------------------

def test_uses_the_lone_commit_subject():
    with patch.object(bot.requests, "get",
                      return_value=_compare(1, ["Fix login redirect on SSO"])) as get:
        assert single_commit_title(P) == "Fix login redirect on SSO"
    assert get.call_args.args[0].endswith("/repos/acme/widgets/compare/main...feat")


def test_only_the_subject_line_of_a_multiline_message():
    body = "Fix login redirect\n\nLonger explanation that belongs in the body, not the title."
    with patch.object(bot.requests, "get", return_value=_compare(1, [body])):
        assert single_commit_title(P) == "Fix login redirect"


def test_multi_commit_compare_has_no_borrowed_title():
    with patch.object(bot.requests, "get", return_value=_compare(3, ["a", "b", "c"])):
        assert single_commit_title(P) is None


def test_total_commits_wins_over_the_capped_commits_array():
    # GitHub caps `commits` at 250 but reports the true count in total_commits.
    with patch.object(bot.requests, "get", return_value=_compare(372, ["only one listed"])):
        assert single_commit_title(P) is None


def test_unreadable_compare_is_not_fatal():
    with patch.object(bot.requests, "get", return_value=_compare(1, ["x"], ok=False)):
        assert single_commit_title(P) is None
    with patch.object(bot.requests, "get", side_effect=bot.requests.RequestException("boom")):
        assert single_commit_title(P) is None


def test_empty_subject_falls_back():
    with patch.object(bot.requests, "get", return_value=_compare(1, ["   "])):
        assert single_commit_title(P) is None


def test_long_subject_is_truncated():
    with patch.object(bot.requests, "get", return_value=_compare(1, ["x" * 400])):
        assert len(single_commit_title(P)) == 250


def test_cross_fork_compare_uses_the_api_head():
    p = dict(P, head_owner="someone", head_branch="feat", api_head="someone:feat")
    with patch.object(bot.requests, "get", return_value=_compare(1, ["Add thing"])) as get:
        assert single_commit_title(p) == "Add thing"
    assert get.call_args.args[0].endswith("/compare/main...someone:feat")


# --- create_pr wiring ------------------------------------------------------

def _created():
    r = MagicMock()
    r.status_code = 201
    r.json.return_value = {"html_url": "u", "number": 1, "title": "t"}
    return r


def test_create_pr_titles_a_single_commit_pr_after_the_commit():
    with patch.object(bot, "_single_commit_title", return_value="Fix login redirect on SSO"), \
         patch.object(bot.requests, "post", return_value=_created()) as post:
        bot.create_pr(dict(P))
    assert post.call_args.kwargs["json"]["title"] == "Fix login redirect on SSO"


def test_create_pr_keeps_branch_format_for_multi_commit():
    with patch.object(bot, "_single_commit_title", return_value=None), \
         patch.object(bot.requests, "post", return_value=_created()) as post:
        bot.create_pr(dict(P))
    assert post.call_args.kwargs["json"]["title"] == "acme:feat → main"


def test_explicit_title_beats_the_commit_subject():
    with patch.object(bot, "_single_commit_title", return_value="Commit subject") as sct, \
         patch.object(bot.requests, "post", return_value=_created()) as post:
        bot.create_pr(dict(P, title="My Title"))
    assert post.call_args.kwargs["json"]["title"] == "My Title"
    sct.assert_not_called()  # no compare lookup when a title was given


# --- merge commits don't count as work -------------------------------------

def test_merge_commit_alongside_one_real_commit_still_borrows_the_title():
    # a branch that's one change plus a "Merge branch 'master' into ..." is
    # still a single-commit PR
    commits = [_commit("fix(analytics): emit date doc-value painless per Elasticsearch version"),
               _commit("Merge branch 'master' into analytics_api", merge=True)]
    with patch.object(bot.requests, "get", return_value=_compare(2, commits=commits)):
        assert single_commit_title(P) == \
            "fix(analytics): emit date doc-value painless per Elasticsearch version"


def test_several_merges_around_one_real_commit():
    commits = [_commit("Merge branch 'master' into x", merge=True),
               _commit("Add the thing"),
               _commit("Merge branch 'master' into x", merge=True)]
    with patch.object(bot.requests, "get", return_value=_compare(3, commits=commits)):
        assert single_commit_title(P) == "Add the thing"


def test_two_real_commits_keep_the_branch_format():
    commits = [_commit("First change"), _commit("Second change"),
               _commit("Merge branch 'master' into x", merge=True)]
    with patch.object(bot.requests, "get", return_value=_compare(3, commits=commits)):
        assert single_commit_title(P) is None


def test_only_merge_commits_has_no_title():
    commits = [_commit("Merge branch 'master' into x", merge=True)]
    with patch.object(bot.requests, "get", return_value=_compare(1, commits=commits)):
        assert single_commit_title(P) is None


def test_truncated_compare_is_never_a_single_commit_pr():
    # GitHub caps `commits` at 250; a longer branch can't be judged from it
    commits = [_commit(f"c{i}", merge=(i > 0)) for i in range(250)]
    with patch.object(bot.requests, "get", return_value=_compare(372, commits=commits)):
        assert single_commit_title(P) is None

"""Tests for tagging the PR requester with the build/deploy status."""
import base64
from unittest.mock import MagicMock, patch

import bot

MARKER = "<!-- pr-raiser:requester=UREQ -->"


def _event(sha="c5fb313", pipeline="pipeline-webui-dashboard-ui-uat-Pipeline",
           stages=":white_check_mark: Source\t:white_check_mark: Build\t:white_check_mark: DeployTo-uat-us",
           ts="1.23"):
    """A CodePipeline-shaped bot message: attachment fields, value-before-title."""
    return {"bot_id": "B1", "ts": ts, "channel": "CB", "text": "",
            "attachments": [{"fields": [
                {"title": pipeline, "value": "SUCCEEDED"},
                {"title": "Stages", "value": stages},
                {"title": "Commit Id", "value": sha}]}]}


def _resolve(pr_num=7017, body=MARKER):
    """Patch the GitHub lookups so a candidate resolves to a marked PR."""
    return (
        patch.object(bot, "list_org_repos", return_value=["dashboard-ui", "resume-ui"]),
        patch.object(bot, "find_pr_for_commit",
                     return_value={"number": pr_num, "html_url": f"https://gh/pr/{pr_num}"}),
        patch.object(bot, "fetch_pr_body", return_value=body),
        patch.object(bot, "mark_pr_notified"),
    )


# --- _build_statuses -------------------------------------------------------

def test_status_deployed_uat():
    assert bot._build_statuses(_event()) == [(":white_check_mark: Deployed on UAT", "deployed-uat")]


def test_status_deployed_prod_is_live():
    ev = _event(stages=":white_check_mark: Build\t:white_check_mark: DeployTo-prod-us")
    assert bot._build_statuses(ev) == [(":white_check_mark: Deployed on Live", "deployed-prod")]


def test_status_prod_uk_is_not_live():
    # The UK production deploy must NOT be tagged "Live" — only prod-us is.
    ev = _event(stages=":white_check_mark: Build\t:white_check_mark: DeployTo-prod-uk")
    assert bot._build_statuses(ev) == []


def test_status_prod_us_wins_over_uk():
    # When both regions succeed in one message, only the US deploy is announced.
    ev = _event(stages=":white_check_mark: DeployTo-prod-uk\t:white_check_mark: DeployTo-prod-us")
    assert bot._build_statuses(ev) == [(":white_check_mark: Deployed on Live", "deployed-prod")]


def test_status_build_failed():
    ev = _event(stages=":x: Build")
    assert bot._build_statuses(ev) == [(":x: Build failed", "build-failed")]


def test_status_deploy_failed():
    ev = _event(stages=":white_check_mark: Build\t:x: DeployTo-staging-us")
    assert bot._build_statuses(ev) == [(":x: Deploy to Staging failed", "deployfail-staging")]


def test_status_in_progress_is_empty():
    ev = _event(stages=":white_check_mark: Source\t:building_construction: Build")
    assert bot._build_statuses(ev) == []


# --- commit URL regex (unchanged) -----------------------------------------

def test_commit_url_regex_extracts_parts():
    m = bot.COMMIT_URL_RE.search("https://github.com/vmockinc/dashboard-ui/commit/24a54c0abc")
    assert m.groups() == ("vmockinc", "dashboard-ui", "24a54c0abc")


# --- handle_build_notification --------------------------------------------

def test_tags_requester_with_deploy_status_in_thread():
    say = MagicMock()
    orgs, findpr, body, mark = _resolve()
    with orgs, findpr, body, mark as marked:
        bot.handle_build_notification(_event(), say)
    kw = say.call_args.kwargs
    assert kw["thread_ts"] == "1.23"
    assert "<@UREQ>" in kw["text"] and "#7017" in kw["text"] and "Deployed on UAT" in kw["text"]
    marked.assert_called_once()  # dedup markers written
    assert marked.call_args.args[-1] == ["deployed-uat"]  # per-status slug


def test_resolves_repo_from_commit_id_field():
    say = MagicMock()
    orgs, findpr, body, mark = _resolve()
    with orgs, findpr as fp, body, mark:
        bot.handle_build_notification(_event(), say)
    fp.assert_called_once_with("vmockinc", "dashboard-ui", "c5fb313", "uat")


def test_fuzzy_matches_non_substring_repo():
    say = MagicMock()
    ev = _event(pipeline="pipeline-jobs-am-uat-Pipeline")
    with patch.object(bot, "list_org_repos", return_value=["jobs-api-am", "jobs-api-es"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 5, "html_url": "u"}) as fp, \
         patch.object(bot, "fetch_pr_body", return_value=MARKER), \
         patch.object(bot, "mark_pr_notified"):
        bot.handle_build_notification(ev, say)
    assert fp.call_args_list[0].args == ("vmockinc", "jobs-api-am", "c5fb313", "uat")
    assert "<@UREQ>" in say.call_args.kwargs["text"]


def test_no_tag_without_requester_marker():
    say = MagicMock()
    with patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 1, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value="ordinary PR body"):
        bot.handle_build_notification(_event(), say)
    say.assert_not_called()


def test_dedupes_already_reported_status():
    say = MagicMock()
    body = MARKER + "\n<!-- pr-raiser:notified:deployed-uat -->"
    with patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 1, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=body), \
         patch.object(bot, "mark_pr_notified"):
        bot.handle_build_notification(_event(), say)
    say.assert_not_called()  # uat deploy already reported


def test_reports_only_fresh_status():
    # uat already reported; this message is a prod deploy -> tag only "Deployed on Live"
    say = MagicMock()
    ev = _event(stages=":white_check_mark: DeployTo-uat-us\t:white_check_mark: DeployTo-prod-us")
    body = MARKER + "\n<!-- pr-raiser:notified:deployed-uat -->"
    with patch.object(bot, "list_org_repos", return_value=["dashboard-ui"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 7, "html_url": "u"}), \
         patch.object(bot, "fetch_pr_body", return_value=body), \
         patch.object(bot, "mark_pr_notified") as mark:
        bot.handle_build_notification(ev, say)
    text = say.call_args.kwargs["text"]
    assert "Deployed on Live" in text and "Deployed on UAT" not in text
    assert mark.call_args.args[-1] == ["deployed-prod"]


def test_in_progress_message_makes_no_lookups():
    say = MagicMock()
    ev = _event(stages=":white_check_mark: Source\t:building_construction: Build")
    with patch.object(bot, "find_pr_for_commit") as fp:
        bot.handle_build_notification(ev, say)
    fp.assert_not_called()
    say.assert_not_called()


def test_no_candidate_repo_no_tag():
    say = MagicMock()
    ev = _event(pipeline="pipeline-unknownthing-uat")
    with patch.object(bot, "list_org_repos", return_value=["dashboard-ui", "resume-ui"]), \
         patch.object(bot, "find_pr_for_commit") as fp:
        bot.handle_build_notification(ev, say)
    fp.assert_not_called()
    say.assert_not_called()


# --- commit -> PR selection -----------------------------------------------

def _pulls_resp(prs):
    r = MagicMock()
    r.ok = True
    r.json.return_value = prs
    return r


def test_picks_the_pr_the_commit_merged_not_the_first():
    # regression: a deploy's merge commit is also carried by an open promotion PR,
    # and GitHub lists that one first; the merged PR is the one with the watcher.
    prs = [{"number": 187, "state": "open", "merged_at": None, "merge_commit_sha": "999"},
           {"number": 517, "state": "closed", "merged_at": "2026-08-10T00:00:00Z",
            "merge_commit_sha": "e130f3ecafe"}]
    with patch.object(bot.requests, "get", return_value=_pulls_resp(prs)):
        assert bot.find_pr_for_commit("o", "r", "e130f3e")["number"] == 517


def test_prefers_merged_pr_when_no_merge_sha_match():
    prs = [{"number": 1, "state": "open", "merged_at": None, "merge_commit_sha": None},
           {"number": 2, "state": "closed", "merged_at": "2026-08-10T00:00:00Z",
            "merge_commit_sha": "abc"}]
    with patch.object(bot.requests, "get", return_value=_pulls_resp(prs)):
        assert bot.find_pr_for_commit("o", "r", "zzz")["number"] == 2


def test_single_pr_is_returned_unchanged():
    with patch.object(bot.requests, "get", return_value=_pulls_resp([{"number": 9}])):
        assert bot.find_pr_for_commit("o", "r", "sha")["number"] == 9


def test_no_prs_for_commit_returns_none():
    with patch.object(bot.requests, "get", return_value=_pulls_resp([])):
        assert bot.find_pr_for_commit("o", "r", "sha") is None


# --- custom per-watcher messages ------------------------------------------

def test_marker_note_round_trips():
    note = "please verify SSO | over 2 lines\nand a --> tricky bit"
    mk = bot._requester_marker("UALICE", note)
    assert mk.startswith("<!-- pr-raiser:requester=UALICE|") and "-->" in mk
    assert bot.watcher_notes(mk) == {"UALICE": note}


def test_plain_marker_has_no_note():
    assert bot.watcher_notes(MARKER) == {"UREQ": ""}


def test_note_wins_over_plain_for_same_user():
    body = MARKER + "\n" + bot._requester_marker("UREQ", "verify once live")
    assert bot.watcher_notes(body) == {"UREQ": "verify once live"}


def test_deploy_message_is_quoted_under_status():
    say = MagicMock()
    enc = base64.b64encode("run the regression".encode()).decode()
    body = f"desc\n<!-- pr-raiser:requester=UREQ -->\n<!-- pr-raiser:requester=UALICE|{enc} -->"
    orgs, findpr, _, mark = _resolve()
    with orgs, findpr, patch.object(bot, "fetch_pr_body", return_value=body), mark:
        bot.handle_build_notification(_event(), say)
    text = say.call_args.kwargs["text"]
    assert "<@UREQ>" in text and "<@UALICE>" in text          # both tagged on the summary line
    assert "> <@UALICE>: run the regression" in text          # alice's custom note quoted
    assert "> <@UREQ>:" not in text                           # the plain watcher gets no note line


# --- routing ---------------------------------------------------------------

def test_bot_message_routes_to_build_notif_not_pr_creation():
    say = MagicMock()
    with patch.object(bot, "create_pr") as cp, \
         patch.object(bot, "handle_build_notification") as hbn:
        bot.handle_message(_event(), say, client=MagicMock(), context={}, logger=None)
    hbn.assert_called_once()
    cp.assert_not_called()


def test_edit_of_bot_message_is_processed():
    # CodePipeline edits one message as it progresses; the deploy outcome arrives
    # as a message_changed edit and must be processed (not dropped).
    say = MagicMock()
    inner = {"bot_id": "B1", "ts": "9.9",
             "attachments": [{"fields": [{"title": "Commit Id", "value": "abc1234"}]}]}
    with patch.object(bot, "handle_build_notification") as hbn:
        bot.handle_message({"subtype": "message_changed", "channel": "C", "message": inner}, say)
    hbn.assert_called_once()
    assert hbn.call_args.args[0] is inner  # the updated message is what's processed


def test_delete_and_human_edit_ignored():
    say = MagicMock()
    with patch.object(bot, "handle_build_notification") as hbn, \
         patch.object(bot, "create_pr") as cp:
        bot.handle_message({"subtype": "message_deleted"}, say)
        # a human message edit (no bot_id inside) is not a build message
        bot.handle_message({"subtype": "message_changed", "message": {"user": "U1"}}, say)
    hbn.assert_not_called()
    cp.assert_not_called()


# --- pipeline branch must match the PR's base branch -----------------------

def test_pipeline_branch_extraction():
    assert bot._pipeline_branch(_event(pipeline="pipeline-cmc-ims-api-schedules-master")) == "master"
    assert bot._pipeline_branch(_event(pipeline="pipeline-jobs-am-uat-Pipeline")) == "uat"
    assert bot._pipeline_branch(_event(pipeline="pipeline-cmc-cmc-accounts-data-sync-uat")) == "uat"
    # an unrecognised suffix must not filter every PR out
    assert bot._pipeline_branch(_event(pipeline="pipeline-some-service-xyz")) is None


def test_master_build_does_not_tag_a_uat_pr():
    # regression (Sai): commit c7b0af2 was introduced by PR #591 targeting uat.
    # Once uat merged into master, the master pipeline's staging deploy tagged
    # that uat PR's author. A master build may only report master PRs.
    with patch.object(bot.requests, "get",
                      return_value=_pulls_resp([{"number": 591, "state": "closed",
                                                 "merged_at": "2026-08-14T00:00:00Z",
                                                 "merge_commit_sha": "271a53d",
                                                 "base": {"ref": "uat"}}])):
        assert bot.find_pr_for_commit("vmockinc", "ims-api-schedules", "c7b0af2", "master") is None
        # the uat pipeline still reports it
        pr = bot.find_pr_for_commit("vmockinc", "ims-api-schedules", "c7b0af2", "uat")
    assert pr["number"] == 591


def test_master_build_tags_the_promotion_pr():
    # the uat->master promotion PR is the right one to tag on a master build
    prs = [{"number": 591, "state": "closed", "merged_at": "2026-08-01T00:00:00Z",
            "merge_commit_sha": "271a53d", "base": {"ref": "uat"}},
           {"number": 640, "state": "closed", "merged_at": "2026-08-14T00:00:00Z",
            "merge_commit_sha": "c7b0af2", "base": {"ref": "master"}}]
    with patch.object(bot.requests, "get", return_value=_pulls_resp(prs)):
        pr = bot.find_pr_for_commit("vmockinc", "ims-api-schedules", "c7b0af2", "master")
    assert pr["number"] == 640


def test_unknown_pipeline_branch_still_matches_on_commit():
    prs = [{"number": 7, "state": "closed", "merged_at": "2026-08-14T00:00:00Z",
            "merge_commit_sha": "abc1234", "base": {"ref": "uat"}}]
    with patch.object(bot.requests, "get", return_value=_pulls_resp(prs)):
        pr = bot.find_pr_for_commit("vmockinc", "x", "abc1234", None)
    assert pr["number"] == 7


def test_master_pipeline_end_to_end_no_tag_for_uat_pr():
    say = MagicMock()
    ev = _event(pipeline="pipeline-cmc-ims-api-schedules-master",
                stages=":white_check_mark: Build\t:white_check_mark: DeployTo-staging-us")
    with patch.object(bot, "list_org_repos", return_value=["ims-api-schedules"]), \
         patch.object(bot.requests, "get",
                      return_value=_pulls_resp([{"number": 591, "state": "closed",
                                                 "merged_at": "2026-08-14T00:00:00Z",
                                                 "merge_commit_sha": "271a53d",
                                                 "base": {"ref": "uat"}}])):
        bot.handle_build_notification(ev, say)
    say.assert_not_called()  # silence beats tagging the wrong person

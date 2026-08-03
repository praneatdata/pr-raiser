"""Tests for tagging the PR requester with the build/deploy status."""
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
    fp.assert_called_once_with("vmockinc", "dashboard-ui", "c5fb313")


def test_fuzzy_matches_non_substring_repo():
    say = MagicMock()
    ev = _event(pipeline="pipeline-jobs-am-uat-Pipeline")
    with patch.object(bot, "list_org_repos", return_value=["jobs-api-am", "jobs-api-es"]), \
         patch.object(bot, "find_pr_for_commit", return_value={"number": 5, "html_url": "u"}) as fp, \
         patch.object(bot, "fetch_pr_body", return_value=MARKER), \
         patch.object(bot, "mark_pr_notified"):
        bot.handle_build_notification(ev, say)
    assert fp.call_args_list[0].args == ("vmockinc", "jobs-api-am", "c5fb313")
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


# --- routing ---------------------------------------------------------------

def test_bot_message_routes_to_build_notif_not_pr_creation():
    say = MagicMock()
    with patch.object(bot, "create_pr") as cp, \
         patch.object(bot, "handle_build_notification") as hbn:
        bot.handle_message(_event(), say, client=MagicMock(), context={}, logger=None)
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

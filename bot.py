"""
PR Raiser — shared bot logic, host-agnostic.

Entry points:
  app.py       — Socket Mode (local dev / Docker), no public URL needed.
  api/index.py — HTTP Events API (Vercel serverless).
"""
import json
import os
import re
import ssl
import logging

import requests
from slack_bolt import App
from slack_sdk import WebClient

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("pr-raiser")

from approvers import APPROVERS
from repo_tokens import TOKEN_ENV_VARS

GITHUB_API = "https://api.github.com"


def _match_repo(mapping, owner, repo):
    """Look up owner/repo in a mapping, honoring an 'owner/*' wildcard (exact wins)."""
    owner, repo = owner.lower(), repo.lower()
    return mapping.get(f"{owner}/{repo}") or mapping.get(f"{owner}/*")


def approver_mentions(p):
    """Space-joined Slack @mentions for the repo's configured approvers ('' if none)."""
    ids = _match_repo(APPROVERS, p["owner"], p["repo"]) or []
    return " ".join(f"<@{uid}>" for uid in ids)


def gh_headers(p):
    """Auth headers for this repo: its mapped token if configured, else GITHUB_TOKEN.

    Exact "owner/repo" entries take precedence over "owner/*" wildcards.
    """
    env_name = _match_repo(TOKEN_ENV_VARS, p["owner"], p["repo"])
    token = os.environ.get(env_name) if env_name else None
    if env_name and not token:
        log.warning("Env var %s from repo_tokens.py is not set; falling back to GITHUB_TOKEN", env_name)
    return {
        "Authorization": f"Bearer {token or os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

# Matches github.com/<owner>/<repo>/compare/<spec>, tolerating Slack's <...|...> wrapping
COMPARE_RE = re.compile(
    r"github\.com/(?P<owner>[^/\s<>|]+)/(?P<repo>[^/\s<>|]+)/compare/(?P<spec>[^\s?#<>|]+)",
    re.IGNORECASE,
)

# Slack renders "@user" in message text as <@U012ABC> or <@U012ABC|display-name>.
MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")

# A GitHub commit URL (if a build bot happens to link one) -> owner, repo, sha.
COMMIT_URL_RE = re.compile(
    r"github\.com/([^/\s\"<>|]+)/([^/\s\"<>|]+)/commit/([0-9a-fA-F]{7,40})", re.IGNORECASE)

# AWS CodePipeline's "View Commit" button links to the AWS console, not GitHub,
# so instead read the short SHA from the "Commit Id" attachment field (see
# _commit_sha_from_event) and resolve the repo from the org repo list via the
# repo name embedded in the pipeline name.
_SHA_RE = re.compile(r"[0-9a-f]{7,40}", re.IGNORECASE)

# Hidden marker embedded in PR bodies recording who asked pr-raiser to open it.
REQUESTER_MARKER = "pr-raiser:requester="
REQUESTER_RE = re.compile(r"pr-raiser:requester=([UW][A-Z0-9]+)")
NOTIFIED_MARKER = "pr-raiser:notified"  # appended after we tag, to dedupe repeat build msgs


def mentioned_user_ids(text, exclude=None):
    """Distinct Slack user IDs @mentioned in `text`, in order, minus `exclude` (the bot)."""
    seen, out = set(), []
    for uid in MENTION_RE.findall(text or ""):
        if uid != exclude and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def _strip_mentions(text):
    """Remove @mention tokens and tidy whitespace — for cleaning a title/body."""
    return re.sub(r"\s{2,}", " ", MENTION_RE.sub("", text or "")).strip()


def dm_approvers(client, user_ids, pr, requester=None, logger=None):
    """DM each user the PR link, asking them to approve. Best-effort, per user.

    `requester` is the Slack user ID of whoever asked to open the PR; it's shown
    as an @mention (which renders their name) so the approver knows who's asking.
    """
    link = f"<{pr['html_url']}|PR #{pr['number']}> — {pr['title']}"
    who = f"<@{requester}> asked you to" if requester else "Please"
    text = f":eyes: {who} review and approve {link}"
    for uid in user_ids:
        try:
            client.chat_postMessage(channel=uid, text=text)
        except Exception as e:  # a bad/unreachable user shouldn't sink the rest
            (logger or log).warning("Couldn't DM approver %s: %s", uid, e)


def _split_top_level(text, sep="|"):
    """Split on `sep`, ignoring separators inside Slack <...> entities.

    Slack wraps links/mentions as <url|label> / <@U123|name>, so a naive split
    on '|' would tear those apart; we only split at depth 0.
    """
    parts, depth, cur = [], 0, []
    for ch in text or "":
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def split_command_title_body(text):
    """`<command> | title | body` -> (command, title|None, body|None) on top-level pipes."""
    parts = _split_top_level(text)
    title = parts[1] if len(parts) > 1 and parts[1] else None
    body = parts[2] if len(parts) > 2 and parts[2] else None
    return parts[0], title, body


def parse_compare(owner, repo, spec):
    """Turn a compare spec (base...head) into the fields GitHub's create-PR API needs."""
    sep = "..." if "..." in spec else ".." if ".." in spec else None
    if not sep:
        return None
    base_part, head_part = spec.split(sep, 1)

    base_branch = base_part.split(":")[-1]  # base is always a ref in the base repo

    hp = head_part.split(":")
    if len(hp) == 1:                 # same repo:         branch
        head_owner, head_branch = owner, hp[0]
    elif len(hp) == 2:               # fork, two-part:    owner:branch
        head_owner, head_branch = hp[0], hp[1]
    else:                            # fork, three-part:  owner:repo:branch
        head_owner, head_branch = hp[0], hp[-1]

    # A cross-fork PR needs "owner:branch"; a same-repo PR just needs "branch".
    api_head = head_branch if head_owner == owner else f"{head_owner}:{head_branch}"

    return {
        "owner": owner,
        "repo": repo,
        "base_branch": base_branch,
        "head_owner": head_owner,
        "head_branch": head_branch,
        "api_head": api_head,
    }


def find_open_pr(p):
    """Look up an already-open PR for this exact head/base pair."""
    r = requests.get(
        f"{GITHUB_API}/repos/{p['owner']}/{p['repo']}/pulls",
        headers=gh_headers(p),
        params={
            "head": f"{p['head_owner']}:{p['head_branch']}",
            "base": p["base_branch"],
            "state": "open",
        },
        timeout=30,
    )
    return r.json()[0] if r.ok and r.json() else None


def find_pr_for_commit(owner, repo, sha):
    """The PR a commit belongs to (e.g. the merge commit's PR), or None."""
    r = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}/pulls",
        headers=gh_headers({"owner": owner, "repo": repo}), timeout=30,
    )
    prs = r.json() if r.ok else []
    return prs[0] if isinstance(prs, list) and prs else None


_ORG_REPOS = None


def list_org_repos(owner):
    """All repo names under `owner` (cached per warm process). Used to resolve a
    build message's repo from the repo name embedded in its pipeline name."""
    global _ORG_REPOS
    if _ORG_REPOS is None:
        names, page = [], 1
        while page <= 10:
            r = requests.get(
                f"{GITHUB_API}/orgs/{owner}/repos",
                headers=gh_headers({"owner": owner, "repo": ""}),
                params={"per_page": 100, "page": page, "type": "all"}, timeout=30,
            )
            if not r.ok or not isinstance(r.json(), list) or not r.json():
                break
            names += [x["name"] for x in r.json()]
            page += 1
        _ORG_REPOS = names
    return _ORG_REPOS


def fetch_pr_body(owner, repo, pr):
    """Full PR body (the commit->PR list may omit it, so fetch when missing)."""
    body = pr.get("body")
    if body is None:
        r = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr['number']}",
            headers=gh_headers({"owner": owner, "repo": repo}), timeout=30,
        )
        body = (r.json().get("body") if r.ok else "") or ""
    return body or ""


def mark_pr_notified(owner, repo, pr, body, slugs):
    """Append per-status notified markers to the PR body so the same build status
    for the same PR isn't reported twice."""
    markers = "".join(f"\n<!-- {NOTIFIED_MARKER}:{s} -->" for s in slugs)
    requests.patch(
        f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr['number']}",
        headers=gh_headers({"owner": owner, "repo": repo}),
        json={"body": f"{body}{markers}"}, timeout=30,
    )


def create_pr(p):
    body = p.get("body") or "Opened automatically from a compare link shared in Slack."
    if p.get("requester"):
        # Hidden marker (invisible in GitHub's rendered view) so a later build
        # notification can tag whoever asked for this PR.
        body += f"\n\n<!-- {REQUESTER_MARKER}{p['requester']} -->"
    payload = {
        "title": p.get("title") or f"{p['head_owner']}:{p['head_branch']} → {p['base_branch']}",
        "head": p["api_head"],
        "base": p["base_branch"],
        "body": body,
        # Must be an explicit False: for cross-fork PRs GitHub defaults this
        # to true, and only the fork's owner may grant it — omitting the key
        # still 422s with fork_collab when the token user isn't the fork owner
        # (same bug as https://github.com/cli/cli/issues/8670).
        "maintainer_can_modify": False,
    }
    r = requests.post(
        f"{GITHUB_API}/repos/{p['owner']}/{p['repo']}/pulls",
        headers=gh_headers(p), json=payload, timeout=30,
    )
    if r.status_code == 201:
        return "created", r.json()
    if r.status_code == 422:                     # usually "a PR already exists"
        existing = find_open_pr(p)
        if existing:
            return "exists", existing
    return "error", r


def _pr_result_text(status, result):
    """Slack text for a create_pr outcome (created / exists / error)."""
    if status == "created":
        return f":rocket: Opened <{result['html_url']}|PR #{result['number']}> — {result['title']}"
    if status == "exists":
        return f":information_source: A PR is already open: <{result['html_url']}|PR #{result['number']}>"
    try:
        body = result.json()
        detail = body.get("message", "")
        for err in body.get("errors", []):
            detail += f"\n• {err.get('message', err)}"
    except Exception:
        detail = result.text[:400]
    return f":x: Couldn't open the PR (HTTP {result.status_code}).\n{detail}"


def _rank_repos(blob, repos):
    """Org repos fuzzily ranked by how well their name matches the build message:
    the fraction of the repo name's tokens present as words, best first. Ties
    break toward an exact substring, then more token hits, then a longer name.
    A repo with zero matching tokens is dropped."""
    words = set(re.findall(r"[a-z0-9]+", blob))
    scored = []
    for r in repos:
        toks = [t for t in re.split(r"[-_.]", r.lower()) if len(t) >= 2]
        hits = sum(1 for t in toks if t in words)
        if toks and hits:
            scored.append(((hits / len(toks), r.lower() in blob, hits, len(r)), r))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [r for _, r in scored]


def _commit_sha_from_event(event):
    """Short SHA from a build message: prefer a structured 'Commit Id' attachment
    field (Slack serializes it as value-before-title, so a plain regex misses it);
    fall back to a hex token near the words 'commit id' in either order."""
    for att in event.get("attachments", []) or []:
        for f in att.get("fields", []) or []:
            if "commit" in (f.get("title") or "").lower():
                mm = _SHA_RE.search(f.get("value") or "")
                if mm:
                    return mm.group(0).lower()
    blob = json.dumps(event).lower()
    for pat in (r"commit id.{0,60}?([0-9a-f]{7,40})", r"([0-9a-f]{7,40}).{0,60}?commit id"):
        mm = re.search(pat, blob, re.DOTALL)
        if mm:
            return mm.group(1)
    return None


def _build_pr_candidates(event):
    """(owner, repo, sha) tuples to try for this build message. Prefers a direct
    github.com commit URL; otherwise uses the 'Commit Id' short SHA and fuzzily
    resolves the repo from the org repo list via the pipeline name. Each candidate
    is confirmed against GitHub by the caller, so a wrong guess just won't match."""
    raw = json.dumps(event)
    m = COMMIT_URL_RE.search(raw)
    if m:
        return [(m.group(1), m.group(2), m.group(3))]
    sha = _commit_sha_from_event(event)
    if not sha:
        return []
    owner = DEFAULT_REPO_OWNER
    return [(owner, r, sha) for r in _rank_repos(raw.lower(), list_org_repos(owner))[:12]]


ENV_LABELS = {"uat": "UAT", "staging": "Staging", "prod": "Live"}
# A stage line in a build message: an emoji then a known stage name.
_STAGE_RE = re.compile(
    r"(:x:|:white_check_mark:|✅|❌)\s*(build|deployto-[a-z]+(?:-[a-z]+)?)\b", re.IGNORECASE)
_OK = {":white_check_mark:", "✅"}
_FAIL = {":x:", "❌"}


def _build_statuses(event):
    """[(headline, slug), ...] for completed stages worth reporting in this build
    message (a deploy that finished, or a failed build). Empty while in progress."""
    stages = {stage: emoji for emoji, stage in _STAGE_RE.findall(json.dumps(event).lower())}
    out = []
    if stages.get("build") in _FAIL:
        out.append((":x: Build failed", "build-failed"))
    for stage, emoji in stages.items():
        m = re.match(r"deployto-([a-z]+)", stage)
        if not m:
            continue
        env = m.group(1)
        label = ENV_LABELS.get(env, env.title())
        if emoji in _OK:
            out.append((f":white_check_mark: Deployed on {label}", f"deployed-{env}"))
        elif emoji in _FAIL:
            out.append((f":x: Deploy to {label} failed", f"deployfail-{env}"))
    return out


def handle_build_notification(event, say, logger=None):
    """If a bot's build message reports a deploy/build outcome for a PR we opened,
    reply in-thread tagging the requester with that status — once per status."""
    statuses = _build_statuses(event)
    if not statuses:
        return  # nothing terminal to report yet (still building)
    try:
        for owner, repo, sha in _build_pr_candidates(event):
            pr = find_pr_for_commit(owner, repo, sha)
            if not pr:
                continue
            body = fetch_pr_body(owner, repo, pr)
            requesters = list(dict.fromkeys(REQUESTER_RE.findall(body)))  # pr-raiser + /track
            if not requesters:
                log.info("build-notif: PR #%s in %s/%s has no requester marker",
                         pr["number"], owner, repo)
                return
            fresh = [(h, s) for h, s in statuses if f"{NOTIFIED_MARKER}:{s}" not in body]
            if not fresh:
                return  # every status here already reported for this PR
            summary = "  ".join(h for h, _ in fresh)
            mentions = " ".join(f"<@{u}>" for u in requesters)
            say(text=f"{mentions} your PR <{pr['html_url']}|#{pr['number']}> — {summary}",
                thread_ts=event.get("ts"))
            mark_pr_notified(owner, repo, pr, body, [s for _, s in fresh])
            return
    except requests.RequestException as e:
        (logger or log).warning("build-notif failed: %s", e)


def handle_message(event, say, client=None, context=None, logger=None):
    subtype = event.get("subtype")
    if subtype == "message_deleted":
        return
    if subtype == "message_changed":
        # Build bots (AWS CodePipeline) post ONE message and edit it as the
        # pipeline runs, so the deploy/build outcome arrives as an edit. Process
        # the updated content (event["message"]) rather than dropping it.
        inner = event.get("message") or {}
        if inner.get("bot_id"):
            handle_build_notification(inner, say, logger)
        return

    if event.get("bot_id") or subtype == "bot_message":
        # Bot messages (e.g. BuildBot) never open PRs, but a build outcome
        # tags the PR's requester.
        handle_build_notification(event, say, logger)
        return
    if subtype:
        return  # joins / other system subtypes

    raw = event.get("text", "") or ""
    cmd_part, title, body = split_command_title_body(raw)
    # Treat pipes as title/body delimiters only when the compare link is in the
    # leading part; an incidental "|" in an ordinary message must not break
    # detection or hijack the title/body.
    if not COMPARE_RE.search(cmd_part):
        cmd_part, title, body = raw, None, None

    match = COMPARE_RE.search(cmd_part)
    if not match:
        return

    thread_ts = event.get("thread_ts") or event.get("ts")
    p = parse_compare(match.group("owner"), match.group("repo"), match.group("spec"))
    if not p:
        say(text=":warning: I found a compare link but couldn't parse it.", thread_ts=thread_ts)
        return
    if title:
        p["title"] = _strip_mentions(title)
    if body:
        p["body"] = _strip_mentions(body)
    p["requester"] = event.get("user")

    log.info("Compare link from %s: %s/%s  %s -> %s",
             event.get("user"), p["owner"], p["repo"], p["api_head"], p["base_branch"])

    try:
        status, result = create_pr(p)
    except requests.RequestException as e:
        say(text=f":x: GitHub request failed: {e}", thread_ts=thread_ts)
        return

    text = _pr_result_text(status, result)
    if status == "created":
        mentions = approver_mentions(p)
        if mentions:
            text += f"\n{mentions} — please review and approve when you can."
    say(text=text, thread_ts=thread_ts)

    if status == "created":
        # DM anyone @mentioned anywhere in the message (excluding the bot) the
        # PR link, asking them to approve.
        bot_id = (context or {}).get("bot_user_id")
        approver_ids = mentioned_user_ids(raw, exclude=bot_id)
        if approver_ids and client is not None:
            dm_approvers(client, approver_ids, result, requester=event.get("user"), logger=logger)


def parse_pr_command(text):
    """Parse `/pr` text into the fields create_pr needs, or None if malformed.

    Accepts:  <owner/repo> <base>...<head>   or   <owner/repo> <base> <head>
    Any @mentions (approvers) are ignored here — the handler pulls those out
    separately. <head> may be a cross-fork spec like someuser:repo:branch.
    """
    refs = MENTION_RE.sub("", text or "")  # drop @approvers before tokenizing
    toks = refs.split()
    if len(toks) < 2 or "/" not in toks[0]:
        return None
    owner, repo = toks[0].split("/", 1)
    rest = toks[1:]
    if len(rest) == 1:
        spec = rest[0]                       # base...head
    elif len(rest) == 2:
        spec = f"{rest[0]}...{rest[1]}"       # base head
    else:
        return None
    return parse_compare(owner, repo, spec)


def handle_pr_command(ack, command, respond, client=None, context=None, logger=None):
    text = command.get("text", "")
    if not text.strip():
        # no args → open the guided form (modal)
        ack()
        if client is not None:
            try:
                client.views_open(trigger_id=command["trigger_id"],
                                  view=build_pr_modal(command.get("channel_id", "")))
            except Exception as e:
                (logger or log).warning("views_open failed: %s", e)
                respond(":warning: Couldn't open the form. Usage: `/pr owner/repo base head`")
        return

    ack()
    cmd_part, title, body = split_command_title_body(text)
    p = parse_pr_command(cmd_part)
    if not p:
        respond(":warning: Usage: `/pr owner/repo base head`\n"
                "• fork PR: `/pr owner/repo base forkowner:branch`\n"
                "• compare style also works: `/pr owner/repo base...head`\n"
                "• custom title/body: `/pr owner/repo base head | Title | Body`")
        return
    if title:
        p["title"] = _strip_mentions(title)
    if body:
        p["body"] = _strip_mentions(body)
    p["requester"] = command.get("user_id")

    try:
        status, result = create_pr(p)
    except requests.RequestException as e:
        respond(f":x: GitHub request failed: {e}")
        return

    # in_channel so the whole channel sees the PR, matching the link flow
    respond(text=_pr_result_text(status, result), response_type="in_channel")

    if status == "created":
        # approvers may be @mentioned anywhere in the command, incl. after the
        # title/body pipes — scan the whole text, not just the command part.
        bot_id = (context or {}).get("bot_user_id")
        approver_ids = mentioned_user_ids(text, exclude=bot_id)
        if approver_ids and client is not None:
            dm_approvers(client, approver_ids, result, requester=command.get("user_id"), logger=logger)


DEFAULT_REPO_OWNER = "vmockinc"  # pre-filled in the /pr form; most PRs are under this org


def _input_block(block_id, label, placeholder=None, multiline=False, optional=False,
                 initial_value=None):
    element = {"type": "plain_text_input", "action_id": "v", "multiline": multiline}
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}
    if initial_value:
        element["initial_value"] = initial_value
    return {"type": "input", "block_id": block_id, "optional": optional,
            "label": {"type": "plain_text", "text": label}, "element": element}


REPO_SELECT_ACTION = "repo_select"


def _repo_block():
    """One field: an external_select that type-ahead-searches org repos AND accepts
    any 'owner/repo' the user types (the options handler offers it verbatim), so
    repos outside the org still work."""
    return {"type": "input", "block_id": "repo",
            "label": {"type": "plain_text", "text": "Repo"},
            "element": {"type": "external_select", "action_id": REPO_SELECT_ACTION,
                        "min_query_length": 0,
                        "placeholder": {"type": "plain_text",
                                        "text": "Pick a repo, or type owner/repo"}}}


def handle_repo_options(ack, payload):
    """Options for the repo external_select: matching org repos, plus — if the user
    typed a full owner/repo — that value verbatim so non-org repos are selectable."""
    query = (payload.get("value") or "").strip()
    q = query.lower()
    try:
        repos = sorted(list_org_repos(DEFAULT_REPO_OWNER))
    except Exception:
        repos = []
    opts = [{"text": {"type": "plain_text", "text": f"{DEFAULT_REPO_OWNER}/{r}"[:75]},
             "value": f"{DEFAULT_REPO_OWNER}/{r}"[:75]}
            for r in repos if q in r.lower()][:50]
    if "/" in query:
        verbatim = {"text": {"type": "plain_text", "text": query[:75]}, "value": query[:75]}
        if verbatim not in opts:
            opts.insert(0, verbatim)
    ack(options=opts[:100])


def build_pr_modal(channel_id=""):
    """The guided PR form opened by a bare `/pr`. channel_id is stashed so the
    submission handler knows where to post the result."""
    approver = {"type": "multi_users_select", "action_id": "v",
                "placeholder": {"type": "plain_text", "text": "Pick teammates (optional)"}}
    return {
        "type": "modal",
        "callback_id": "pr_modal",
        "private_metadata": channel_id or "",
        "title": {"type": "plain_text", "text": "Open a PR"},
        "submit": {"type": "plain_text", "text": "Open PR"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            _repo_block(),
            _input_block("base", "Base branch", "main"),
            _input_block("head", "Head branch", "my-feature  or  forkowner:branch"),
            _input_block("title", "Title (optional)", optional=True),
            _input_block("body", "Body (optional)", multiline=True, optional=True),
            {"type": "input", "block_id": "approvers", "optional": True,
             "label": {"type": "plain_text", "text": "Request approval from"},
             "element": approver},
        ],
    }


def _modal_value(state, block, key="value"):
    """Pull one field's value from a modal's state, regardless of its action_id."""
    inner = next(iter(state.get(block, {}).values()), {})
    return inner.get(key)


def _post_modal_result(client, channel, requester, text, logger=None):
    """Post the result to the channel the form was opened from, else DM the requester."""
    if client is None:
        return
    for target in (channel, requester):
        if not target:
            continue
        try:
            client.chat_postMessage(channel=target, text=text)
            return
        except Exception as e:
            (logger or log).warning("modal result to %s failed: %s", target, e)


def handle_pr_modal_submission(ack, body, view, client=None, context=None, logger=None):
    state = view["state"]["values"]
    repo_field = next(iter(state.get("repo", {}).values()), {})
    repo_full = ((repo_field.get("selected_option") or {}).get("value") or "").strip()
    base = (_modal_value(state, "base") or "").strip()
    head = (_modal_value(state, "head") or "").strip()
    if "/" not in repo_full:
        ack(response_action="errors", errors={"repo": "Pick a repo, or type owner/repo"})
        return
    ack()  # close the modal

    owner, repo = repo_full.split("/", 1)
    p = parse_compare(owner, repo, f"{base}...{head}")
    title = (_modal_value(state, "title") or "").strip()
    body_text = (_modal_value(state, "body") or "").strip()
    if title:
        p["title"] = title
    if body_text:
        p["body"] = body_text

    requester = body["user"]["id"]
    channel = view.get("private_metadata") or ""
    p["requester"] = requester

    try:
        status, result = create_pr(p)
    except requests.RequestException as e:
        _post_modal_result(client, channel, requester, f":x: GitHub request failed: {e}", logger)
        return

    _post_modal_result(client, channel, requester, _pr_result_text(status, result), logger)

    if status == "created":
        bot_id = (context or {}).get("bot_user_id")
        approvers = [a for a in (_modal_value(state, "approvers", "selected_users") or [])
                     if a != bot_id]
        if approvers and client is not None:
            dm_approvers(client, approvers, result, requester=requester, logger=logger)


# A pull request reference: a github.com/.../pull/N link, or owner/repo#N / owner/repo N.
PR_URL_RE = re.compile(r"github\.com/([^/\s<>|]+)/([^/\s<>|]+)/pull/(\d+)", re.IGNORECASE)
PR_REF_RE = re.compile(r"([\w.-]+)/([\w.-]+)[#\s]+(\d+)")


def handle_track_command(ack, command, respond, context=None, logger=None):
    """`/track <PR> [@people]` — stamp requester markers onto a PR so the caller
    (and anyone @mentioned) get the same #code-builds build/deploy tags, even for
    PRs not opened through me."""
    ack()
    text = command.get("text", "") or ""
    m = PR_URL_RE.search(text) or PR_REF_RE.search(text)
    if not m:
        respond(":warning: Usage: `/track <pull-request link> [@teammates]`  "
                "(or `/track owner/repo 123`)")
        return
    owner, repo, number = m.group(1), m.group(2), m.group(3)
    bot_id = (context or {}).get("bot_user_id")
    watchers = [w for w in dict.fromkeys(
        [command.get("user_id")] + mentioned_user_ids(text, exclude=bot_id)) if w]
    p = {"owner": owner, "repo": repo}
    try:
        r = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
                         headers=gh_headers(p), timeout=30)
        if not r.ok:
            respond(f":x: Couldn't find PR #{number} in {owner}/{repo} (HTTP {r.status_code}).")
            return
        pr = r.json()
        body = pr.get("body") or ""
        new = [w for w in watchers if f"{REQUESTER_MARKER}{w}" not in body]
        if not new:
            respond(f":information_source: Already tracking <{pr['html_url']}|#{number}>.")
            return
        markers = "".join(f"\n<!-- {REQUESTER_MARKER}{w} -->" for w in new)
        pr_r = requests.patch(f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
                              headers=gh_headers(p), json={"body": body + markers}, timeout=30)
        if not pr_r.ok:
            respond(f":x: Couldn't update PR #{number} (HTTP {pr_r.status_code}) — "
                    "I may not have write access to that repo.")
            return
        who = " ".join(f"<@{w}>" for w in new)
        respond(f":eyes: Tracking <{pr['html_url']}|#{number}> for {who} — "
                "I'll tag them in #code-builds as it builds and deploys.")
    except requests.RequestException as e:
        respond(f":x: GitHub request failed: {e}")


def build_app(process_before_response=False, token_verification=True):
    """Build a Bolt App wired with the message listener.

    process_before_response=True is required on serverless hosts (Vercel):
    listeners must finish before the HTTP response is returned, because the
    process is frozen/killed right after responding.
    """
    # The corporate TLS proxy (VMock CA) re-signs certificates without the
    # Authority Key Identifier extension, which Python 3.13+'s strict
    # verification rejects. Keep full verification but drop the strict flag.
    ssl_context = ssl.create_default_context()
    ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    app = App(
        client=WebClient(token=os.environ["SLACK_BOT_TOKEN"], ssl=ssl_context),
        signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
        process_before_response=process_before_response,
        token_verification_enabled=token_verification,
    )
    app.event("message")(handle_message)
    app.command("/pr")(handle_pr_command)
    app.command("/track")(handle_track_command)
    app.view("pr_modal")(handle_pr_modal_submission)
    app.options(REPO_SELECT_ACTION)(handle_repo_options)
    return app

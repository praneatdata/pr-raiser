"""
PR Raiser — shared bot logic, host-agnostic.

Entry points:
  app.py       — Socket Mode (local dev / Docker), no public URL needed.
  api/index.py — HTTP Events API (Vercel serverless).
"""
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


def mentioned_user_ids(text, exclude=None):
    """Distinct Slack user IDs @mentioned in `text`, in order, minus `exclude` (the bot)."""
    seen, out = set(), []
    for uid in MENTION_RE.findall(text or ""):
        if uid != exclude and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def dm_approvers(client, user_ids, pr, logger=None):
    """DM each user the PR link, asking them to approve. Best-effort, per user."""
    link = f"<{pr['html_url']}|PR #{pr['number']}> — {pr['title']}"
    text = f":eyes: Please review and approve {link}"
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


def create_pr(p):
    payload = {
        "title": p.get("title") or f"{p['head_owner']}:{p['head_branch']} → {p['base_branch']}",
        "head": p["api_head"],
        "base": p["base_branch"],
        "body": p.get("body") or "Opened automatically from a compare link shared in Slack.",
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


def handle_message(event, say, client=None, context=None, logger=None):
    if event.get("bot_id") or event.get("subtype"):
        return  # ignore bots (incl. ourselves) and edits / joins / etc.

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
        p["title"] = title
    if body:
        p["body"] = body

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
        # DM anyone @mentioned in the command part (excluding the bot) the PR
        # link, asking them to approve.
        bot_id = (context or {}).get("bot_user_id")
        approver_ids = mentioned_user_ids(cmd_part, exclude=bot_id)
        if approver_ids and client is not None:
            dm_approvers(client, approver_ids, result, logger)


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
    ack()
    text = command.get("text", "")
    cmd_part, title, body = split_command_title_body(text)
    p = parse_pr_command(cmd_part)
    if not p:
        respond(":warning: Usage: `/pr owner/repo base head`\n"
                "• fork PR: `/pr owner/repo base forkowner:branch`\n"
                "• compare style also works: `/pr owner/repo base...head`\n"
                "• custom title/body: `/pr owner/repo base head | Title | Body`")
        return
    if title:
        p["title"] = title
    if body:
        p["body"] = body

    try:
        status, result = create_pr(p)
    except requests.RequestException as e:
        respond(f":x: GitHub request failed: {e}")
        return

    # in_channel so the whole channel sees the PR, matching the link flow
    respond(text=_pr_result_text(status, result), response_type="in_channel")

    if status == "created":
        bot_id = (context or {}).get("bot_user_id")
        approver_ids = mentioned_user_ids(cmd_part, exclude=bot_id)
        if approver_ids and client is not None:
            dm_approvers(client, approver_ids, result, logger)


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
    return app

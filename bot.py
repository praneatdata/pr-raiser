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

GITHUB_API = "https://api.github.com"
GH_HEADERS = {
    "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Matches github.com/<owner>/<repo>/compare/<spec>, tolerating Slack's <...|...> wrapping
COMPARE_RE = re.compile(
    r"github\.com/(?P<owner>[^/\s<>|]+)/(?P<repo>[^/\s<>|]+)/compare/(?P<spec>[^\s?#<>|]+)",
    re.IGNORECASE,
)


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
        headers=GH_HEADERS,
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
        "title": f"{p['head_owner']}:{p['head_branch']} → {p['base_branch']}",
        "head": p["api_head"],
        "base": p["base_branch"],
        "body": "Opened automatically from a compare link shared in Slack.",
        "maintainer_can_modify": True,
    }
    r = requests.post(
        f"{GITHUB_API}/repos/{p['owner']}/{p['repo']}/pulls",
        headers=GH_HEADERS, json=payload, timeout=30,
    )
    if r.status_code == 201:
        return "created", r.json()
    if r.status_code == 422:                     # usually "a PR already exists"
        existing = find_open_pr(p)
        if existing:
            return "exists", existing
    return "error", r


def handle_message(event, say, logger):
    if event.get("bot_id") or event.get("subtype"):
        return  # ignore bots (incl. ourselves) and edits / joins / etc.

    match = COMPARE_RE.search(event.get("text", "") or "")
    if not match:
        return

    thread_ts = event.get("thread_ts") or event.get("ts")
    p = parse_compare(match.group("owner"), match.group("repo"), match.group("spec"))
    if not p:
        say(text=":warning: I found a compare link but couldn't parse it.", thread_ts=thread_ts)
        return

    log.info("Compare link from %s: %s/%s  %s -> %s",
             event.get("user"), p["owner"], p["repo"], p["api_head"], p["base_branch"])

    try:
        status, result = create_pr(p)
    except requests.RequestException as e:
        say(text=f":x: GitHub request failed: {e}", thread_ts=thread_ts)
        return

    if status == "created":
        say(text=f":rocket: Opened <{result['html_url']}|PR #{result['number']}> — {result['title']}",
            thread_ts=thread_ts)
    elif status == "exists":
        say(text=f":information_source: A PR is already open: <{result['html_url']}|PR #{result['number']}>",
            thread_ts=thread_ts)
    else:
        try:
            body = result.json()
            detail = body.get("message", "")
            for err in body.get("errors", []):
                detail += f"\n• {err.get('message', err)}"
        except Exception:
            detail = result.text[:400]
        say(text=f":x: Couldn't open the PR (HTTP {result.status_code}).\n{detail}",
            thread_ts=thread_ts)


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
    return app

"""
HTTP Events API entry point — Vercel serverless function.

vercel.json rewrites every path to /api/index/<original-path>, so this one
function serves all routes. Vercel's platform may deliver the WSGI PATH_INFO
as the original path ('/slack/events') or as the rewrite destination with the
original appended ('/api/index/slack/events'); we route on the path *suffix*
so it works either way.

Requires SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET and GITHUB_TOKEN set in the
Vercel project's environment variables.
"""
import logging
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request

log = logging.getLogger("pr-raiser")
app = Flask(__name__)

# Initialize guarded, so a misconfiguration (missing env var, import problem)
# surfaces as a readable error instead of an opaque FUNCTION_INVOCATION_FAILED.
_init_error = None
try:
    # NB: must not be named `handler` — Vercel's Python runtime treats a
    # module-level `handler` as a BaseHTTPRequestHandler class.
    from slack_bolt.adapter.flask import SlackRequestHandler

    from bot import build_app

    bolt_app = build_app(process_before_response=True, token_verification=False)
    slack_request_handler = SlackRequestHandler(bolt_app)
except Exception:
    _init_error = traceback.format_exc()


def _debug_payload(observed_path):
    """Self-checks: what's configured and what broke. Never exposes secret values."""
    import kv
    from repo_tokens import TOKEN_ENV_VARS

    required = {"SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "GITHUB_TOKEN"}
    required.update(TOKEN_ENV_VARS.values())
    return {
        "commit": os.environ.get("VERCEL_GIT_COMMIT_SHA", "unknown")[:7],
        "python": sys.version.split()[0],
        "observed_path": observed_path,  # what Vercel actually handed Flask
        "env": {name: bool(os.environ.get(name)) for name in sorted(required)},
        "kv_configured": kv.kv_available(),  # /track uses the KV store when True
        "init_ok": _init_error is None,
        "init_error": _init_error,
    }


def _run_leaderboard_cron():
    """Post last month's leaderboard (Vercel Cron hits this on the 1st).

    Guarded two ways: CRON_SECRET must match when it's set, and the post itself
    is idempotent per month, so an unauthenticated hit or a platform retry can
    at worst trigger a month that was already announced — which is a no-op.
    """
    if _init_error:
        return {"error": "app failed to initialize; see GET /"}, 500
    secret = os.environ.get("CRON_SECRET")
    if secret and request.headers.get("Authorization") != f"Bearer {secret}":
        return {"error": "unauthorized"}, 401
    try:
        import leaderboard
        # ?dry=1 renders without posting, so the endpoint can be checked safely.
        dry = request.args.get("dry") in ("1", "true", "yes")
        return leaderboard.post_monthly(bolt_app.client, dry_run=dry)
    except Exception:
        log.exception("leaderboard cron failed")
        return {"error": traceback.format_exc().splitlines()[-1]}, 500


@app.route("/", defaults={"subpath": ""}, methods=["GET", "POST"])
@app.route("/<path:subpath>", methods=["GET", "POST"])
def route(subpath):
    tail = "/" + subpath  # e.g. "/slack/events" or "/api/index/slack/events"

    if request.method == "POST" and tail.endswith("/slack/events"):
        if _init_error:
            return {"error": "app failed to initialize; see GET /"}, 500
        # Slack retries an event when our first response misses its 3s ack
        # deadline (common on serverless cold starts). That first invocation
        # still runs to completion and posts the reply, so ack retried event
        # deliveries without reprocessing — otherwise every slow cold start
        # posts a duplicate. Never skip url_verification (a setup handshake).
        if request.headers.get("X-Slack-Retry-Num"):
            body = request.get_json(silent=True) or {}
            if body.get("type") == "event_callback":
                log.info("Skipping Slack retry #%s (reason: %s)",
                         request.headers.get("X-Slack-Retry-Num"),
                         request.headers.get("X-Slack-Retry-Reason"))
                return "", 200
        return slack_request_handler.handle(request)

    if tail.endswith("/cron/leaderboard"):
        return _run_leaderboard_cron()

    if tail.endswith("/debug"):
        return _debug_payload(tail)

    if _init_error:
        return f"<pre>init failed:\n\n{_init_error}</pre>", 500
    return "PR Raiser is running."

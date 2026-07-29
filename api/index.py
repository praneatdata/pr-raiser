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
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request

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
    from repo_tokens import TOKEN_ENV_VARS

    required = {"SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "GITHUB_TOKEN"}
    required.update(TOKEN_ENV_VARS.values())
    return {
        "commit": os.environ.get("VERCEL_GIT_COMMIT_SHA", "unknown")[:7],
        "python": sys.version.split()[0],
        "observed_path": observed_path,  # what Vercel actually handed Flask
        "env": {name: bool(os.environ.get(name)) for name in sorted(required)},
        "init_ok": _init_error is None,
        "init_error": _init_error,
    }


@app.route("/", defaults={"subpath": ""}, methods=["GET", "POST"])
@app.route("/<path:subpath>", methods=["GET", "POST"])
def route(subpath):
    tail = "/" + subpath  # e.g. "/slack/events" or "/api/index/slack/events"

    if request.method == "POST" and tail.endswith("/slack/events"):
        if _init_error:
            return {"error": "app failed to initialize; see GET /"}, 500
        return slack_request_handler.handle(request)

    if tail.endswith("/debug"):
        return _debug_payload(tail)

    if _init_error:
        return f"<pre>init failed:\n\n{_init_error}</pre>", 500
    return "PR Raiser is running."

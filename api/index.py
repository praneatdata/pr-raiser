"""
HTTP Events API entry point — Vercel serverless function.

Slack POSTs every event to /slack/events; vercel.json rewrites all paths to
this function. Requires SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET and GITHUB_TOKEN
set in the Vercel project's environment variables.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request

app = Flask(__name__)

# Initialize lazily-guarded so a misconfiguration (missing env var, import
# problem) surfaces as a readable error on GET / instead of an opaque
# FUNCTION_INVOCATION_FAILED crash.
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


@app.route("/slack/events", methods=["POST"])
def slack_events():
    if _init_error:
        return {"error": "app failed to initialize; see GET /"}, 500
    return slack_request_handler.handle(request)


@app.route("/", methods=["GET"])
def health():
    if _init_error:
        return f"<pre>init failed:\n\n{_init_error}</pre>", 500
    return "PR Raiser is running."

"""
HTTP Events API entry point — Vercel serverless function.

Slack POSTs every event to /slack/events; vercel.json rewrites all paths to
this function. Requires SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET and GITHUB_TOKEN
set in the Vercel project's environment variables.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request
from slack_bolt.adapter.flask import SlackRequestHandler

from bot import build_app

# token_verification=False skips an auth.test round-trip on every cold start.
bolt_app = build_app(process_before_response=True, token_verification=False)
handler = SlackRequestHandler(bolt_app)

app = Flask(__name__)


@app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)


@app.route("/", methods=["GET"])
def health():
    return "PR Raiser is running."

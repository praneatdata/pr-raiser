"""
Socket Mode entry point — for local dev and the Docker container.
Needs no public URL, but the Slack app must have Socket Mode enabled.

NB: this file must NOT be named app.py / main.py / index.py / server.py —
Vercel's zero-config Python detection would pick it up as the web app's
entrypoint and serve it instead of api/index.py.
"""
import os

from slack_bolt.adapter.socket_mode import SocketModeHandler

from bot import build_app, log

app = build_app()

if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    log.info("PR Raiser is up (Socket Mode). Waiting for compare links…")
    handler.start()

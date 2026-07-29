"""Test bootstrap: set dummy env vars before app modules import.

bot.build_app() reads SLACK_BOT_TOKEN at call time and api/index imports it at
module load, so these must exist before collection imports anything. setdefault
means a real .env (loaded by bot via python-dotenv, override=False) never
clobbers these, and tests stay hermetic — all network calls are mocked.
"""
import os

os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("GITHUB_TOKEN", "ghp_default_token")

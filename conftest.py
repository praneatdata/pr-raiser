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

import pytest


@pytest.fixture(autouse=True)
def _no_org_discovery(monkeypatch):
    """Keep tests hermetic and independent of the developer's .env.

    gh_headers falls back to org discovery for repos not in repo_tokens.py, which
    would otherwise hit the real GitHub API. Stub the per-token listing (tests that
    exercise discovery override it), drop real GITHUB_TOKEN_* vars that python-dotenv
    loaded (so token discovery doesn't vary by machine), and clear the shared cache.
    """
    import bot

    monkeypatch.setattr(bot, "_list_org_repos_with", lambda owner, token_env: [])
    # create_pr asks GitHub for the compare when no title was given, to borrow a
    # lone commit's subject. Default it to "no single commit" so existing tests
    # stay offline; the title tests import the real function directly.
    monkeypatch.setattr(bot, "_single_commit_title", lambda p: None)
    for name in [n for n in os.environ if n.startswith("GITHUB_TOKEN_")]:
        monkeypatch.delenv(name, raising=False)
    bot._ORG_DISCOVERY.clear()
    yield
    bot._ORG_DISCOVERY.clear()

"""
Repo → GitHub token mapping, for repos the default GITHUB_TOKEN can't reach.

Keys are "owner/repo" in lowercase. Values are ENV VAR NAMES (not tokens! —
this repo is public), holding a token of an account with access to that repo.
Repos not listed here fall back to GITHUB_TOKEN.

After adding an entry, set the env var in Vercel (and .env for local use)
and redeploy — env changes don't apply to existing deployments.
"""
TOKEN_ENV_VARS = {
    # "vmockinc/resume-ui": "GITHUB_TOKEN_SAKSHAM",
}

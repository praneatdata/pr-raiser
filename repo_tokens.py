"""
Repo → GitHub token mapping, for repos the default GITHUB_TOKEN can't reach.

Keys are "owner/repo" or an owner-level wildcard "owner/*", in lowercase;
an exact repo entry beats the owner wildcard. Values are ENV VAR NAMES (not
tokens! — this repo is public), holding a token of an account with access to
that repo. Repos matching neither fall back to GITHUB_TOKEN.

After adding an entry, set the env var in Vercel (and .env for local use)
and redeploy — env changes don't apply to existing deployments.
"""
TOKEN_ENV_VARS = {
    "vmockinc/jobs-curation": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/employer-api-relations": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-placement-management": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/ims-api-lst": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/api-network-database": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/dashboard-api-notifications": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-notification": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-calendar-management-system": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-student-management": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-student-view": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-resume-management": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-reporting": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-community-user-management": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-communications": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/dashboard-api-survey": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-campaigns": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-community-configuration": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-websockets-server": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-es": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-er": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/employer-api-jobs": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-notes": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-accounts-data-sync": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-am": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-student-analytics": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-accounts": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-survey": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-students": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/cmc-webhook-server": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/employer-api-applications": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-newsletter": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/ims-api-schedules": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-analytics": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-communications": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-tracking": "GITHUB_TOKEN_SAGNIK",
    "vmockinc/jobs-api-community-config": "GITHUB_TOKEN_SAGNIK",
}

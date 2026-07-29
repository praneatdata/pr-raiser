"""
Repo → Slack users to notify for approval when the bot opens a PR.

Keys are "owner/repo" or an owner-level wildcard "owner/*", in lowercase; an
exact repo entry beats the wildcard. Values are lists of Slack member IDs
(open a person's Slack profile → ⋯ → "Copy member ID"), NOT display names —
@mentions only render from IDs.

Approvers are tagged only when a PR is newly opened, not when one already
exists, so re-sharing a link doesn't re-ping them.
"""
APPROVERS = {
    # "vmockinc/resume-ui": ["U012ABC3DEF", "U045GHI6JKL"],
    # "vmockinc/*": ["U012ABC3DEF"],
}

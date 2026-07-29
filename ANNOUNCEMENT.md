<!-- Paste-ready Slack message (mrkdwn). Send AFTER the `improvements` branch
     is deployed — both features below only work once it's live.
     Copy everything below this comment. -->

:sparkles: *What's new in PR Raiser*

Two new ways to open PRs faster:

*1.* :new: *`/pr` — no compare link needed*
Give me the repo, base, and head, and I'll open the PR and post the link right here:
`/pr vmockinc/resume-ui main my-feature`
Opening from a fork? Put the fork owner in the head:
`/pr vmockinc/resume-ui uat yourname:your-branch`

*2.* :new: *Loop in an approver automatically*
@mention a teammate along with your compare link (or with `/pr`) and I'll DM them the PR asking for their review:
`github.com/vmockinc/resume-ui/compare/main...my-feature @teammate`

*3.* :new: *Set a custom title & body*
Add them after a `|` (works with both a link and `/pr`; body is optional):
`/pr vmockinc/resume-ui main my-feature | Fix login redirect | Closes the loop bug on SSO`

_As always, you can still just paste a compare link and I'll open the PR for you._

Happy shipping! :tada:

# PR Raiser

A Slack bot that opens GitHub pull requests from Slack and then keeps you posted
as they build and deploy.

Paste a GitHub **compare link** in a channel the bot is in and it opens the PR
for you. Or use `/pr` for a guided form. Once the PR is in, it tags you in
**#code-builds** when the pipeline builds and deploys it, so you don't have to
watch the channel. At the start of each month it posts a leaderboard of who
raised the most PRs.

```
you:       https://github.com/vmockinc/resume-ui/compare/uat...my-feature
pr-raiser: 🚀 Opened PR #482 — Fix score tooltip on Safari
   …later, in #code-builds:
pr-raiser: @you your PR #482 — ✅ Deployed on UAT
```

---

## What it does

| | |
|---|---|
| **Opens PRs from a link** | Any `github.com/<owner>/<repo>/compare/<base>...<head>` link posted in a channel becomes a PR. Several links in one message → one PR each. |
| **Cross-fork PRs** | `forkowner:branch` and `forkowner:repo:branch` heads work. |
| **Smart titles** | A single-commit PR is titled after that commit's subject (like GitHub does); multi-commit PRs fall back to `head-owner:head → base`. |
| **Approver DMs** | @mention teammates and each gets a DM with the PR link and who asked. |
| **Deploy tracking** | Tags you in-thread in #code-builds on ✅ Deployed on UAT / Staging / Live and ❌ Build failed. |
| **Deploy notes** | Attach a message ("run the SSO regression once this is live") delivered to a teammate when the PR reaches each stage. |
| **Monthly leaderboard** | Posted automatically on the 1st: who raised how many PRs last month, plus all-time totals. |
| **Multi-token GitHub access** | Uses several GitHub accounts' tokens and figures out which one can see which repo. |

---

## Commands

### Paste a compare link (no command needed)

```
https://github.com/vmockinc/resume-ui/compare/uat...my-feature
https://github.com/vmockinc/resume-ui/compare/uat...my-feature | Custom title | Custom body
```

@mention anyone in the message to DM them for approval. Multiple compare links
in one message open one PR each, sharing the same title/body.

### `/pr` — open a PR without a compare link

```
/pr                                          → opens the guided form
/pr owner/repo base head
/pr owner/repo base...head                   → compare style
/pr owner/repo base forkowner:branch         → cross-fork PR
/pr owner/repo base head @teammate           → also DM them to approve
/pr owner/repo base head | Title | Body
/pr owner/repo base head @teammate | Title | Body | Message on deploy
```

The bare `/pr` form has: a type-ahead **Repo** picker (searches the org, and
accepts any `owner/repo` you type), **Base**, **Head**, optional **Title** and
**Body**, a native **Request approval from** people picker, and a **Message on
deploy** field.

### `/track` — follow a PR the bot didn't open

```
/track https://github.com/vmockinc/resume-ui/pull/1234
/track owner/repo 1234
/track <pr-link> @teammate
/track <pr-link> @teammate | run the SSO regression once this is live
```

You (and anyone you @mention) get tagged in #code-builds as that PR builds and
deploys. Tracking is stored in a KV store keyed by `owner/repo#number`, so it
needs no write access to the repo.

---

## Recent features

Newest first.

- **One-round-trip `/track`** (Aug 2026) — `/track` now does a single KV write
  and no GitHub read at all, so it comfortably beats Slack's 3-second command
  deadline on a cold serverless start.
- **Clearer access errors + token health** — a GitHub 404 on a private repo now
  says the token may be missing access or expired instead of looking like a typo.
  `GET /debug?tokens=1` reports each GitHub token as `ok as <login>` / `INVALID`
  / `unset`.
- **Leaderboard dry run** — `GET /cron/leaderboard?dry=1` renders the message
  without posting or consuming the month; `&month=YYYY-MM` previews a specific
  month.
- **Monthly PR leaderboard** — posted to the raising channel on the 1st at 04:00
  UTC via Vercel Cron. Counts are incremented as PRs are opened (no history
  scan), bucketed in IST, and seeded with a frozen pre-counter baseline so
  all-time totals didn't restart at zero. Idempotent — a retry can't double-post.
- **Commit-derived PR titles** — a compare holding exactly one real commit is
  titled after that commit. Merge commits don't count, so `feature + "Merge
  branch 'master'"` still counts as a one-commit PR.
- **Branch-aware deploy tagging** — only PRs targeting the branch the pipeline
  actually deployed are tagged, so a master build no longer re-tags the author of
  the original UAT PR.
- **Race-safe build notifications** — each build status is claimed atomically
  (Redis `SADD`) before posting, because CodePipeline edits its message and Slack
  retries, so invocations overlap. Failed posts release the claim for a retry.
- **Correct PR for a deploy commit** — a commit can belong to several PRs; the
  bot picks the one it actually merged, not the first GitHub lists.
- **Multiple compare links per message** — one PR per *distinct* target (Slack's
  `<url|label>` wrapping used to double-match the same link).
- **Automatic repo → token discovery** — the org is listed with every configured
  `GITHUB_TOKEN_*` and the results are remembered, so granting access to more
  repos is just setting one more env var. `repo_tokens.py` is now only for
  overrides.
- **KV-backed watchers** — watcher state moved out of hidden PR-body markers into
  the KV store, so `/track` works on PRs the bot has no push access to. Body
  markers still work as a fallback when no KV is configured.
- **Per-teammate deploy messages** — `| Message` on `/pr` and `/track`, and the
  *Message on deploy* form field.
- **`/track`** — follow any PR's build and deploy progress.
- **Guided `/pr` form** — combined repo dropdown + free text in one
  `external_select`, native approver picker, `vmockinc/` pre-filled.
- **Build/deploy notifications** — including handling CodePipeline's edit-one-
  message-as-it-runs behavior, and treating `prod-us` (not `prod-uk`) as "Live".
- **Custom title and body** via `| Title | Body`.
- **Approver DMs** naming who requested the review.

---

## How it works

```
Slack ──▶ /slack/events ──▶ api/index.py (Flask on Vercel)
                                 │
                                 ├─▶ bot.py       compare links, /pr, /track, modal
                                 ├─▶ kv.py        Upstash Redis REST — watchers, dedup, tallies
                                 └─▶ leaderboard.py  monthly post (Vercel Cron → /cron/leaderboard)
```

| File | Purpose |
|---|---|
| [bot.py](bot.py) | All bot logic, host-agnostic — parsing, GitHub calls, Slack handlers. |
| [api/index.py](api/index.py) | HTTP Events entry point for Vercel; also serves `/debug` and `/cron/leaderboard`. |
| [socket_mode.py](socket_mode.py) | Socket Mode entry point for local dev / Docker (no public URL needed). |
| [kv.py](kv.py) | Minimal Upstash Redis REST client — no SDK, one command per HTTP call. |
| [leaderboard.py](leaderboard.py) | Monthly leaderboard message + idempotent posting. |
| [repo_tokens.py](repo_tokens.py) | Repo → GitHub token env-var **overrides** (values are env var *names*, never tokens). |
| [approvers.py](approvers.py) | Repo → Slack member IDs to ping for approval. |
| [manifest.yaml](manifest.yaml) | Slack app manifest (scopes, commands, event subscriptions). |

**KV keys**

| Key | Type | Holds |
|---|---|---|
| `prwatch:<owner>/<repo>#<n>` | hash | `slack_uid → deploy note` (watchers) |
| `prnotif:<owner>/<repo>#<n>` | set | build statuses already reported (dedup / claims) |
| `prlb:YYYY-MM` | hash | PRs opened that month, per user |
| `prlb:total` | hash | PRs opened all time, per user |
| `prlb:posted` | set | months already announced |

---

## Setup

1. Create the Slack app from [manifest.yaml](manifest.yaml) and install it to the
   workspace. Invite the bot to the channel where compare links are pasted and to
   **#code-builds**.
2. Copy `.env.example` to `.env` and fill in:

   | Variable | Needed for |
   |---|---|
   | `SLACK_BOT_TOKEN` | always |
   | `SLACK_SIGNING_SECRET` | HTTP mode (Vercel) |
   | `SLACK_APP_TOKEN` | Socket Mode only |
   | `GITHUB_TOKEN` | always — classic PAT, `repo` scope |
   | `GITHUB_TOKEN_*` | extra accounts' tokens for repos the default can't reach |
   | `KV_REST_API_URL` / `KV_REST_API_TOKEN` | `/track` + leaderboard (or the `UPSTASH_REDIS_REST_*` pair) |
   | `CRON_SECRET` | authenticates the leaderboard cron endpoint |
   | `LEADERBOARD_CHANNEL`, `LEADERBOARD_MAINTAINER` | optional leaderboard overrides |

3. Add approvers in [approvers.py](approvers.py) if you want automatic review
   pings per repo.

Without a KV configured everything still works — `/track` falls back to writing
hidden markers into the PR body, which requires push access to that repo.

## Running

**Vercel (production).** `vercel.json` rewrites every path to `api/index.py` and
registers the monthly cron. Env-var changes require a redeploy to take effect.

**Local / Docker (Socket Mode).**

```sh
pip install -r requirements.txt
python socket_mode.py
# or
docker build -t pr-raiser . && docker run --env-file .env pr-raiser
```

**Tests.**

```sh
pip install -r requirements-dev.txt
pytest
```

## Health checks

| Endpoint | What it tells you |
|---|---|
| `GET /` | "PR Raiser is running." (or the init traceback) |
| `GET /debug` | commit, Python version, which env vars are set, whether KV is configured, init status |
| `GET /debug?tokens=1` | validity of every GitHub token (one API call each) |
| `GET /cron/leaderboard?dry=1` | renders next post without sending it |

## Notes

- `socket_mode.py` must **not** be renamed to `app.py` / `main.py` / `index.py` /
  `server.py` — Vercel's zero-config Python detection would serve it instead of
  `api/index.py`.
- The corporate TLS proxy re-signs certificates without an Authority Key
  Identifier, which Python 3.13+ rejects; `bot.build_app()` keeps full
  verification but clears `VERIFY_X509_STRICT`. The Docker image trusts
  `vmock-ca.crt` for the same reason.
- Slack retries an event when the first response misses its 3s ack deadline
  (common on cold starts). `api/index.py` acks retries without reprocessing, since
  the first invocation still runs to completion.

"""
Tiny Upstash-Redis REST client — the watcher/dedup store for /track.

Why: /track used to record watchers by editing the PR body, which needs *push*
access the bot usually lacks on other people's PRs (GitHub masks the denied
write as a 404). Storing state in a KV keyed by owner/repo#number needs no write
to the target repo — only that the bot can *read* the PR.

No SDK/deps: Upstash's REST API takes one command as a JSON array over HTTP,
which works fine on Vercel's serverless runtime.

Config (set in Vercel + .env, then redeploy):
  KV_REST_API_URL / KV_REST_API_TOKEN            (Vercel KV / Upstash-via-Vercel), or
  UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN   (Upstash direct)
If neither pair is set, kv_available() is False and callers fall back to the old
PR-body-marker behavior, so nothing breaks before the store is provisioned.
"""
import os

import requests


def _config():
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return url, token


def kv_available():
    url, token = _config()
    return bool(url and token)


def _command(cmd, timeout=10):
    """Run one Redis command (a list like ["HSET", key, field, val]) and return
    its `result`. Raises requests.RequestException on transport/HTTP error, which
    callers already catch and degrade on."""
    url, token = _config()
    if not (url and token):
        raise RuntimeError("KV not configured")
    r = requests.post(url.rstrip("/"), headers={"Authorization": f"Bearer {token}"},
                      json=[str(c) for c in cmd], timeout=timeout)
    r.raise_for_status()
    return r.json().get("result")


def hset(key, field, value, nx=False):
    """Set hash field. nx=True (HSETNX) only sets when the field is absent, so a
    plain re-add never clobbers an existing note."""
    return _command(["HSETNX" if nx else "HSET", key, field, value])


def hgetall(key):
    """Return the hash as a dict (Upstash returns a flat [f1, v1, f2, v2, ...])."""
    res = _command(["HGETALL", key]) or []
    return {res[i]: res[i + 1] for i in range(0, len(res) - 1, 2)}


def sadd(key, *members):
    if not members:
        return 0
    return _command(["SADD", key, *members])


def smembers(key):
    return _command(["SMEMBERS", key]) or []

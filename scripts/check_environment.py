#!/usr/bin/env python3
"""Fail-closed GitHub environment gate, called both BEFORE and AFTER approval.

Never fetches secret values. Uses only read-only GitHub Actions API requests.
A missing/unprotected Backup-Production environment is a hard stop.
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen


class EnvironmentGateError(RuntimeError):
    pass


def verify_execution_context(env: dict[str, str]) -> None:
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise EnvironmentGateError("BACKUP_DENIED: manual workflow_dispatch required")
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise EnvironmentGateError("BACKUP_DENIED: main only")
    sha = env.get("GITHUB_SHA", "")
    expected = env.get("BACKUP_EXPECTED_SHA", "")
    if not re.fullmatch("[0-9a-f]{40}", sha) or sha != expected:
        raise EnvironmentGateError("BACKUP_DENIED: expected exact main SHA mismatch")
    if env.get("BACKUP_OPERATION") != "BACKUP_ONLY_NO_RESTORE":
        raise EnvironmentGateError("BACKUP_DENIED: explicit backup-only operation required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", env.get("GITHUB_REPOSITORY", "")):
        raise EnvironmentGateError("BACKUP_DENIED: malformed repository identity")
    if not env.get("GITHUB_TOKEN"):
        raise EnvironmentGateError("BACKUP_DENIED: API read token missing")


def get_json(path: str, token: str) -> dict:
    request = Request(
        "https://api.github.com" + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
    except Exception as exc:
        raise EnvironmentGateError("BACKUP_DENIED: GitHub environment check unavailable") from exc
    if not isinstance(data, dict):
        raise EnvironmentGateError("BACKUP_DENIED: environment response malformed")
    return data


def verify_environment_policy(metadata: dict, branches: dict) -> None:
    if metadata.get("name", "").lower() != "backup-production":
        raise EnvironmentGateError("BACKUP_DENIED: protected environment missing")
    rules = metadata.get("protection_rules")
    if not isinstance(rules, list):
        raise EnvironmentGateError("BACKUP_DENIED: environment protection unknown")
    reviewer_rules = [r for r in rules if r.get("type") == "required_reviewers"]
    if len(reviewer_rules) != 1 or not reviewer_rules[0].get("reviewers"):
        raise EnvironmentGateError("BACKUP_DENIED: environment required reviewers missing")
    policy = metadata.get("deployment_branch_policy")
    if not isinstance(policy, dict) or policy.get("custom_branch_policies") is not True:
        raise EnvironmentGateError("BACKUP_DENIED: environment must restrict allowed branches")
    entries = branches.get("branch_policies", [])
    if branches.get("total_count") != 1 or not isinstance(entries, list) or len(entries) != 1:
        raise EnvironmentGateError("BACKUP_DENIED: only main may enter backup environment")
    entry = entries[0]
    if entry.get("name") != "main" or entry.get("type") not in ("branch", None):
        raise EnvironmentGateError("BACKUP_DENIED: backup environment branch policy must be exactly main")


def verify_environment(env: dict[str, str]) -> None:
    verify_execution_context(env)
    repo = env["GITHUB_REPOSITORY"]
    token = env["GITHUB_TOKEN"]
    prefix = "/repos/" + repo + "/environments/" + quote("Backup-Production", safe="")
    metadata = get_json(prefix, token)
    branches = get_json(prefix + "/deployment-branch-policies?per_page=100", token)
    verify_environment_policy(metadata, branches)


def main() -> int:
    try:
        verify_environment(dict(os.environ))
        print("BACKUP GATE PASS: manual exact-main dispatch, protected reviewer environment and main-only policy.")
        return 0
    except EnvironmentGateError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

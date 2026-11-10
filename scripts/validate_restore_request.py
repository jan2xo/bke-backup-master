#!/usr/bin/env python3
"""Strictly validate GitHub repository_dispatch recovery request, never execute restore."""
import json
import os
from pathlib import Path
import re
import sys
import uuid


class RequestDenied(RuntimeError):
    pass


FIELDS = {"request_id", "source", "intent", "source_commit"}


def verify(event, env):
    if env.get("GITHUB_EVENT_NAME") != "repository_dispatch":
        raise RequestDenied("RESTORE_DENIED: repository_dispatch required")
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise RequestDenied("RESTORE_DENIED: default main required")
    if env.get("GITHUB_REPOSITORY") != "jan2xo/bke-backup-master":
        raise RequestDenied("RESTORE_DENIED: repository mismatch")
    if event.get("action") != "bke_restore_requested":
        raise RequestDenied("RESTORE_DENIED: wrong event type")
    sender = event.get("sender")
    allowed = env.get("RESTORE_DISPATCH_SENDER_ID", "")
    if not re.fullmatch(r"[1-9][0-9]{1,19}", allowed):
        raise RequestDenied("RESTORE_DENIED: sender not configured")
    if not isinstance(sender, dict) or type(sender.get("id")) is not int or str(sender["id"]) != allowed:
        raise RequestDenied("RESTORE_DENIED: sender identity not allowlisted")
    payload = event.get("client_payload")
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise RequestDenied("RESTORE_DENIED: unexpected request fields")
    if payload.get("source") != "bke-digital-solutions" or payload.get("intent") != "RESTORE_ISOLATED_REQUEST_ONLY":
        raise RequestDenied("RESTORE_DENIED: source or intent mismatch")
    if not isinstance(payload.get("source_commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", payload["source_commit"]):
        raise RequestDenied("RESTORE_DENIED: immutable source SHA required")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str):
        raise RequestDenied("RESTORE_DENIED: UUID v4 required")
    try:
        parsed = uuid.UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise RequestDenied("RESTORE_DENIED: malformed request id") from exc
    if parsed.version != 4 or str(parsed) != request_id:
        raise RequestDenied("RESTORE_DENIED: canonical random UUID v4 required")
    return request_id


def main():
    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_path:
            raise RequestDenied("RESTORE_DENIED: GitHub event path missing")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        request_id = verify(event, os.environ)
        print("REQUEST ACCEPTED: request_id=" + request_id + "; NO RESTORE EXECUTED")
        return 0
    except (RequestDenied, ValueError, OSError, UnicodeError) as exc:
        print(str(exc) if isinstance(exc, RequestDenied) else "RESTORE_DENIED: malformed event", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

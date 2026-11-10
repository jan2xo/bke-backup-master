import unittest
import uuid
from scripts import validate_restore_request as r


def environment():
    return {
        "GITHUB_EVENT_NAME": "repository_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "jan2xo/bke-backup-master",
        "RESTORE_DISPATCH_SENDER_ID": "163499503",
    }


def request():
    return {
        "action": "bke_restore_requested",
        "sender": {"id": 163499503, "login": "jan2xo"},
        "client_payload": {
            "request_id": str(uuid.uuid4()),
            "source": "bke-digital-solutions",
            "intent": "RESTORE_ISOLATED_REQUEST_ONLY",
            "source_commit": "a" * 40,
        }
    }


class RecoveryRequestTests(unittest.TestCase):
    def test_authenticated_request_is_accepted_but_only_a_request(self):
        incoming = request()
        self.assertEqual(r.verify(incoming, environment()), incoming["client_payload"]["request_id"])

    def test_wrong_caller_and_unconfigured_allowlist_rejected(self):
        source = request()
        invalid = (
            {"RESTORE_DISPATCH_SENDER_ID": ""},
            {"RESTORE_DISPATCH_SENDER_ID": "another-id"},
            {"RESTORE_DISPATCH_SENDER_ID": "9999"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REPOSITORY": "someone/other"},
        )
        for delta in invalid:
            with self.subTest(delta=delta), self.assertRaises(r.RequestDenied):
                r.verify(source, {**environment(), **delta})

    def test_refuse_database_url_archive_key_and_target_in_payload(self):
        for extra in ("database_url", "target", "archive_key", "age_identity", "callback_url"):
            modified = request()
            modified["client_payload"][extra] = "production"
            with self.subTest(extra=extra), self.assertRaises(r.RequestDenied):
                r.verify(modified, environment())

    def test_invalid_intent_source_sha_event_or_nonce(self):
        for k, value in (
            ("intent", "RESTORE_PRODUCTION"),
            ("source", "another-app"),
            ("source_commit", "not-a-commit"),
            ("source_commit", "A"*40),
            ("request_id", "bad-uuid"),
            ("request_id", str(uuid.uuid1())),
        ):
            modified = request()
            modified["client_payload"][k] = value
            with self.subTest(field=k), self.assertRaises(r.RequestDenied):
                r.verify(modified, environment())
        evt = request()
        evt["action"] = "other"
        with self.assertRaises(r.RequestDenied):
            r.verify(evt, environment())


if __name__ == "__main__":
    unittest.main()

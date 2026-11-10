import os
import unittest
from pathlib import Path
from unittest.mock import patch
import bke_backup_master as b

class PolicyTests(unittest.TestCase):
    def test_neon_direct_and_restricted(self):
        good = "postgresql://read:pass@ep-sample.ap-southeast-1.aws.neon.tech/sample?sslmode=require"
        self.assertEqual(b.validate_neon_url(good)["PGDATABASE"], "sample")
        for candidate in (
            good.replace("ep-sample.", "ep-sample-pooler."),
            good.replace("sslmode=require", "sslmode=disable"),
            good.replace(".neon.tech", ".example.com"),
            good + "&options=bad",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(b.BackupError):
                b.validate_neon_url(candidate)

    def test_key_and_receipt_validation(self):
        key = b.make_key()
        self.assertRegex(key, b.KEY_RE)
        self.assertNotEqual(key, b.make_key())
        receipt = {"version": 1, "object_key": key, "ciphertext_size": 123,
                   "ciphertext_sha256": "a" * 64,
                   "archive_format": "postgresql17-custom-age-x25519"}
        b.validate_receipt(receipt, key)
        for change in ({"version": 2}, {"object_key": "wrong"},
                       {"ciphertext_size": -1}, {"ciphertext_sha256": "bad"}):
            with self.assertRaises(b.BackupError):
                b.validate_receipt({**receipt, **change}, key)

    def test_destination_rejection_even_if_docker_available(self):
        for key in ("DATABASE_URL", "NEON_READONLY_DATABASE_URL", "PGHOST",
                    "RESTORE_TARGET_DATABASE_URL", "PRODUCTION_DATABASE_URL"):
            with self.subTest(key=key), patch.dict(os.environ, {key:"sensitive"}, clear=True):
                with patch.object(b.shutil, "which", return_value="/usr/bin/exists"):
                    with self.assertRaisesRegex(b.BackupError, "unsafe restore"):
                        b.isolation_preflight()

    def test_rest_limit_and_recipient_rejection(self):
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_size = b.LIMIT + 1
            with self.assertRaisesRegex(b.BackupError, "300 MB"):
                b.upload_rest(Path("/unused"), b.make_key(), account="a"*32,
                              bucket="bucket-name", token="fake")
        with self.assertRaisesRegex(b.BackupError, "AGE_RECIPIENT"):
            b.dump_encrypted(Path("/unused"), "invalid", "AGE-SECRET-KEY-private")

if __name__ == "__main__":
    unittest.main()

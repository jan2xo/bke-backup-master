import unittest
from unittest.mock import patch
from scripts import check_environment as g


def valid_context():
    return {
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a"*40,
        "BACKUP_EXPECTED_SHA": "a"*40,
        "BACKUP_OPERATION": "BACKUP_ONLY_NO_RESTORE",
        "BACKUP_AUTOMATION_ENABLED": "true",
        "GITHUB_REPOSITORY": "jan2xo/bke-backup-master",
        "GITHUB_TOKEN": "synthetic",
    }


def configured():
    return {
        "name": "Backup-Production",
        "protection_rules": [],
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
    }


def branches():
    return {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}


class ScheduledEnvironmentTests(unittest.TestCase):
    def test_daily_schedule_and_manual_only_when_armed(self):
        g.verify_execution_context(valid_context())
        g.verify_execution_context({**valid_context(), "GITHUB_EVENT_NAME": "workflow_dispatch"})
        invalid = (
            {"GITHUB_EVENT_NAME": "push"}, {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "repository_dispatch"},
            {"BACKUP_AUTOMATION_ENABLED": ""}, {"BACKUP_AUTOMATION_ENABLED": "false"},
            {"GITHUB_REF": "refs/heads/feature"}, {"GITHUB_SHA": "b"*40},
            {"BACKUP_EXPECTED_SHA": "b"*40}, {"BACKUP_OPERATION": "RESTORE_PRODUCTION"},
            {"GITHUB_REPOSITORY": "other/repo"}, {"GITHUB_TOKEN": ""},
        )
        for delta in invalid:
            with self.subTest(delta=delta), self.assertRaises(g.EnvironmentGateError):
                g.verify_execution_context({**valid_context(), **delta})

    def test_no_reviewer_needed_but_exact_main_branch_required(self):
        g.verify_environment_policy(configured(), branches())
        for invalid_meta, invalid_branches in (
            ({**configured(), "name": "Other"}, branches()),
            ({**configured(), "protection_rules": None}, branches()),
            ({**configured(), "deployment_branch_policy": None}, branches()),
            ({**configured(), "deployment_branch_policy": {"custom_branch_policies": False}}, branches()),
            (configured(), {"total_count": 0, "branch_policies": []}),
            (configured(), {"total_count": 2, "branch_policies": [{"name": "main"}, {"name": "*"}]}),
            (configured(), {"total_count": 1, "branch_policies": [{"name": "*", "type": "branch"}]}),
            (configured(), {"total_count": 1, "branch_policies": [{"name": "main", "type": "tag"}]}),
        ):
            with self.subTest(meta=invalid_meta, policies=invalid_branches), self.assertRaises(g.EnvironmentGateError):
                g.verify_environment_policy(invalid_meta, invalid_branches)

    def test_github_api_error_does_not_allow_export(self):
        with patch.object(g, "get_json", side_effect=g.EnvironmentGateError("API unavailable")):
            with self.assertRaises(g.EnvironmentGateError):
                g.verify_environment(valid_context())

    def test_environment_check_must_fetch_metadata_and_branch_policy(self):
        with patch.object(g, "get_json", side_effect=[configured(), branches()]) as api:
            g.verify_environment(valid_context())
            self.assertEqual(api.call_count, 2)


if __name__ == "__main__":
    unittest.main()

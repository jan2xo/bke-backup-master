import unittest
from unittest.mock import patch
from scripts import check_environment as g


def valid_context():
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "a" * 40, "BACKUP_EXPECTED_SHA": "a" * 40,
        "BACKUP_OPERATION": "BACKUP_ONLY_NO_RESTORE",
        "GITHUB_REPOSITORY": "jan2xo/bke-backup-master", "GITHUB_TOKEN": "synthetic",
    }


def protected():
    return {
        "name": "Backup-Production",
        "protection_rules": [{
            "type": "required_reviewers",
            "reviewers": [{"type": "User", "reviewer": {"login": "jan2xo"}}],
        }],
        "deployment_branch_policy": {
            "protected_branches": False, "custom_branch_policies": True
        },
    }


def branch_policy():
    return {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}


class PublicRepositoryGateTests(unittest.TestCase):
    def test_exact_main_manual_dispatch(self):
        g.verify_execution_context(valid_context())
        attacks = (
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "push"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_SHA": "b" * 40},
            {"BACKUP_EXPECTED_SHA": "b" * 40},
            {"BACKUP_OPERATION": "RESTORE"},
            {"GITHUB_REPOSITORY": "other"},
            {"GITHUB_TOKEN": ""},
        )
        for patch_values in attacks:
            with self.subTest(patch_values=patch_values), self.assertRaises(g.EnvironmentGateError):
                g.verify_execution_context({**valid_context(), **patch_values})

    def test_explicit_reviewer_and_main_only_branch(self):
        g.verify_environment_policy(protected(), branch_policy())
        cases = (
            ({**protected(), "protection_rules": []}, branch_policy()),
            ({**protected(), "protection_rules": [{"type": "wait_timer"}]}, branch_policy()),
            ({**protected(), "protection_rules": [{"type": "required_reviewers", "reviewers": []}]}, branch_policy()),
            ({**protected(), "deployment_branch_policy": None}, branch_policy()),
            ({**protected(), "deployment_branch_policy": {"custom_branch_policies": False}}, branch_policy()),
            (protected(), {"total_count": 0, "branch_policies": []}),
            (protected(), {"total_count": 2, "branch_policies": [{"name": "main"}, {"name": "*"}]}),
            (protected(), {"total_count": 1, "branch_policies": [{"name": "*", "type": "branch"}]}),
            (protected(), {"total_count": 1, "branch_policies": [{"name": "main", "type": "tag"}]}),
        )
        for meta, policy in cases:
            with self.subTest(meta=meta, policy=policy), self.assertRaises(g.EnvironmentGateError):
                g.verify_environment_policy(meta, policy)

    def test_unreachable_environment_fails_closed(self):
        with patch.object(g, "get_json", side_effect=OSError("fixture api unavailable")):
            with self.assertRaises(OSError):
                g.verify_environment(valid_context())

    def test_protection_is_checked_both_before_and_after_review(self):
        with patch.object(g, "get_json", side_effect=[protected(), branch_policy()]) as api:
            g.verify_environment(valid_context())
            self.assertEqual(api.call_count, 2)


if __name__ == "__main__":
    unittest.main()

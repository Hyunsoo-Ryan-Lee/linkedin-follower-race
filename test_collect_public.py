import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_public import collect, parse_count


class PublicCollectionTests(unittest.TestCase):
    def test_exact_metadata_count(self):
        markup = '<meta name="description" content="Databricks | 1,450,013 followers on LinkedIn. The data + AI company">'
        self.assertEqual(parse_count(markup, "databricks"), 1450013)

    def test_follow_action_not_employee_or_like_statistics(self):
        node = {"@type": "Organization", "name": "Snowflake", "url": "https://www.linkedin.com/company/snowflake-computing/", "numberOfEmployees": 1234, "interactionStatistic": [{"interactionType": "https://schema.org/LikeAction", "userInteractionCount": 99}, {"interactionType": "https://schema.org/FollowAction", "userInteractionCount": 1450422}]}
        markup = '<script type="application/ld+json">' + json.dumps({"@graph": [node]}) + '</script>'
        self.assertEqual(parse_count(markup, "snowflake"), 1450422)

    def test_authwalls_wrong_companies_rounded_and_conflicting_counts_fail(self):
        for markup in ["<title>Sign in | LinkedIn</title>", '<meta name="description" content="Snowflake | 1,450,422 followers on LinkedIn">', '<meta name="description" content="Databricks | 1M followers on LinkedIn">', '<meta name="description" content="Databricks | 1,45,013 followers on LinkedIn">', '<meta name="description" content="Databricks | 1,450,013 followers on LinkedIn"><meta property="og:description" content="Databricks | 1,450,014 followers on LinkedIn">']:
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                parse_count(markup, "databricks")

    def test_partial_failure_preserves_verified_history_and_timestamps(self):
        data = {"schemaVersion": 1, "observations": [{"observedAt": "2026-11-21T10:13:44.825Z", "databricks": 1450013, "snowflake": 1450422}]}
        previous = copy.deepcopy(data["observations"])
        def fetch(company):
            return {"state": "ok", "count": 1450500} if company == "databricks" else {"state": "blocked", "httpStatus": 999}
        updated = collect(data, fetch)
        self.assertEqual(updated["observations"], previous)
        self.assertEqual(updated["collection"]["state"], "blocked")
        self.assertEqual(updated["collection"]["lastSuccessAt"], previous[-1]["observedAt"])

    def test_success_writes_one_complete_new_observation(self):
        data = {"schemaVersion": 1, "observations": []}
        updated = collect(data, lambda company: {"state": "ok", "count": 1450013 if company == "databricks" else 1450422})
        self.assertEqual(len(updated["observations"]), 1)
        self.assertEqual(updated["collection"]["state"], "ok")
        self.assertEqual(updated["observations"][0]["databricks"], 1450013)

    def test_manual_run_does_not_claim_a_scheduled_success(self):
        data = {"schemaVersion": 1, "observations": []}
        with patch.dict('os.environ', {'GITHUB_EVENT_NAME': 'workflow_dispatch'}):
            result = collect(data, lambda _: {"state": "ok", "count": 100})
        self.assertEqual(result['collection']['event'], 'workflow_dispatch')
        self.assertIsNone(result['collection']['lastScheduledSuccessAt'])

    def test_scheduled_success_survives_a_later_manual_run(self):
        data = {"schemaVersion": 1, "observations": []}
        with patch.dict('os.environ', {'GITHUB_EVENT_NAME': 'schedule'}):
            result = collect(data, lambda _: {"state": "ok", "count": 100})
        scheduled = result['collection']['lastScheduledSuccessAt']
        self.assertEqual(scheduled, result['observations'][-1]['observedAt'])
        with patch.dict('os.environ', {'GITHUB_EVENT_NAME': 'workflow_dispatch'}):
            result = collect(result, lambda _: {"state": "ok", "count": 101})
        self.assertEqual(result['collection']['lastScheduledSuccessAt'], scheduled)

    def test_dashboard_request_remains_identifiable_after_later_updates(self):
        request = 'e55e8345-34fd-4ff4-8fe8-c3a6b5e1d872'
        data = {"schemaVersion": 1, "observations": []}
        with patch.dict('os.environ', {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'DASHBOARD_REQUEST_ID': request}):
            result = collect(data, lambda _: {"state": "ok", "count": 100})
        self.assertEqual(result['observations'][-1]['requestId'], request)
        self.assertEqual(result['requests'][-1]['id'], request)
        with patch.dict('os.environ', {'GITHUB_EVENT_NAME': 'schedule', 'DASHBOARD_REQUEST_ID': ''}):
            result = collect(result, lambda _: {"state": "ok", "count": 101})
        self.assertEqual(result['requests'][-1]['id'], request)
        self.assertNotIn('requestId', result['observations'][-1])

    def test_failed_dashboard_request_is_recorded_without_inventing_an_observation(self):
        request = 'e55e8345-34fd-4ff4-8fe8-c3a6b5e1d872'
        data = {"schemaVersion": 1, "observations": []}
        with patch.dict('os.environ', {'DASHBOARD_REQUEST_ID': request}):
            result = collect(data, lambda _: {"state": "blocked", "httpStatus": 999})
        self.assertEqual(result['requests'][-1]['state'], 'blocked')
        self.assertEqual(result['observations'], [])


if __name__ == "__main__":
    unittest.main()

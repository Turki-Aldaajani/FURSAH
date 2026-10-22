"""First working path: health/ready, discovery and details over the prepared data.

Run from backend/: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
The classroom evaluation time is pinned here so results do not drift with the date.
"""
import csv
import os
import unittest
from pathlib import Path

os.environ["FURSAH_EVALUATION_TIME"] = "2026-10-18T12:00:00+03:00"
os.environ["FURSAH_MODEL_API_KEY"] = ""  # automated tests never call a real model, whatever backend/.env holds
os.environ["FURSAH_TRACE_JSONL_PATH"] = ""  # ...and never write into the real trace file named in backend/.env

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "raw_opportunities.csv"
NO_PROFILE = {"interests": [], "skills": None}


def discover(client, query, constraints=None, resolution=None):
    body = {"query": query, "profile": NO_PROFILE, "constraints": constraints or {}}
    if resolution:
        body["constraint_resolution"] = resolution
    response = client.post("/api/v1/discover", json=body)
    return response.status_code, response.json()


class FirstPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        with RAW.open(encoding="utf-8-sig", newline="") as stream:
            cls.raw_ids = {row["source_id"] for row in csv.DictReader(stream)}

    def details(self, identifier):
        response = self.client.get(f"/api/v1/opportunities/{identifier}")
        return response.status_code, response.json()

    def assert_traceable(self, body):
        for rec in body["recommendations"]:
            self.assertTrue(rec["source_ids"])
            self.assertTrue(set(rec["source_ids"]) <= self.raw_ids)
            status, det = self.details(rec["opportunity_id"])
            self.assertEqual(status, 200)
            self.assertEqual(det["source_ids"], rec["source_ids"])

    def test_health_and_ready_reflect_loaded_store(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        response = self.client.get("/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"storage": True, "application": True})

    def test_valid_request_returns_typed_traceable_results(self):
        status, body = discover(self.client, "أبحث عن ورشة")
        self.assertEqual((status, body["status"]), (200, "ok"))
        self.assertTrue(1 <= len(body["recommendations"]) <= 3)
        for rec in body["recommendations"]:
            self.assertEqual(rec["opportunity_type"], "workshop")
            self.assertEqual(rec["eligibility_status"], "uncertain")
            self.assertNotIn(rec["application_status"], ("closed", "cancelled"))
        self.assert_traceable(body)

    def test_unmatched_topic_is_no_match(self):
        status, body = discover(self.client, "ورشة في الحوسبة الكمومية للمريخ")
        self.assertEqual((status, body["status"], body["recommendations"]), (200, "no_match", []))

    def test_unknown_fee_is_never_free(self):
        status, det = self.details("SRC-0004")  # "لم تُحدد رسوم المشاركة"
        self.assertEqual(status, 200)
        self.assertIsNone(det["cost"])
        status, det = self.details("SRC-0008")  # fee not mentioned at all
        self.assertIsNone(det["cost"])
        status, body = discover(self.client, "فرصة تطوع في تبوك", {"max_budget_sar": 0})
        self.assertEqual(body["status"], "ok")
        for rec in body["recommendations"]:
            _, d = self.details(rec["opportunity_id"])
            self.assertIn(d["cost"], (0, 0.0, None))
            if d["cost"] is None:
                self.assertTrue(any("الرسوم" in c for c in rec["caveats"]))
        self.assert_traceable(body)

    def test_conflicting_fee_stays_unresolved(self):
        status, det = self.details("SRC-0172")
        self.assertIsNone(det["cost"])
        self.assertTrue(any("120" in w and "135" in w for w in det["data_warnings"]))

    def test_missing_or_near_deadline_is_flagged(self):
        status, det = self.details("SRC-0001")  # no deadline stated
        self.assertIsNone(det["deadline"])
        self.assertTrue(any("موعد التقديم" in w for w in det["data_warnings"]))
        status, body = discover(self.client, "دورة مجانية عن بعد")
        for rec in body["recommendations"]:
            if rec["deadline"] in (None, "2026-10-18"):  # a same-day deadline has no cutoff time: uncertain
                self.assertTrue(any("موعد التقديم" in c for c in rec["caveats"]))

    def test_details_by_source_id_returns_the_linked_opportunity(self):
        status, det = self.details("SRC-0007")
        self.assertEqual(status, 200)
        self.assertEqual(det["source_ids"], ["SRC-0006", "SRC-0007"])
        self.assertEqual(self.details(det["opportunity_id"])[1]["opportunity_id"], det["opportunity_id"])
        self.assertEqual(self.details("SRC-9999")[0], 404)

    def test_cancelled_is_never_recommended(self):
        status, det = self.details("SRC-0062")
        self.assertEqual(det["application_status"], "cancelled")
        status, body = discover(self.client, "تدريب عملي في الأحساء")
        self.assertNotIn(det["opportunity_id"], [r["opportunity_id"] for r in body["recommendations"]])

    def test_query_city_conflicting_with_saved_city_needs_info(self):
        status, body = discover(self.client, "ورشة في جدة", {"location_preference": ["الرياض"]})
        self.assertEqual(body["status"], "needs_info")
        self.assertTrue(body["constraint_conflict"])
        self.assertEqual(body["missing_fields"], ["constraints.location_preference"])
        status, body = discover(self.client, "ورشة في جدة", {"location_preference": ["الرياض"]}, "query")
        self.assertEqual(body["status"], "ok")
        for rec in body["recommendations"]:
            self.assertTrue(rec["location"] == "جدة" or rec["delivery_mode"] == "online")


if __name__ == "__main__":
    unittest.main()

"""Tests for Opportunity Radar.

The protocol and honesty tests run offline against a stubbed source, so they
pass in CI with no network. The two tests marked `live` hit grants.gov and are
skipped unless RADAR_LIVE=1, because a hackathon judge should be able to run
the suite on a plane.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from radar import server, tools
from radar.sources import SourceError

LIVE = os.environ.get("RADAR_LIVE") == "1"

FAKE_SEARCH = {
    "hitCount": 2,
    "oppHits": [
        {"id": "111", "number": "AI-1", "title": "AI for Small Clinics",
         "agency": "NIH", "closeDate": "12/31/2099"},
        {"id": "222", "number": "AI-2", "title": "Undated Program",
         "agency": "NSF", "closeDate": ""},
    ],
}
FAKE_DETAIL = {
    "id": "111", "opportunityTitle": "AI for Small Clinics", "opportunityNumber": "AI-1",
    "synopsis": {
        "agencyName": "NIH", "awardCeiling": "500000", "awardFloor": "50000",
        "responseDate": "Dec 31, 2099 12:00:00 AM EST", "numberOfAwards": 4,
        "applicantTypes": [{"description": "Small businesses"}],
        "synopsisDesc": "<p>Funding for  clinic AI tools.</p>",
    },
}


class ToolContract(unittest.TestCase):
    def test_find_reports_source_totals(self):
        with mock.patch("radar.tools.search_grants", return_value=FAKE_SEARCH):
            r = tools.find_opportunities("ai", 5)
        self.assertEqual(r["total_matches"], 2)
        self.assertEqual(r["opportunities"][0]["id"], "111")

    def test_detail_parses_money_and_long_date_format(self):
        with mock.patch("radar.tools.fetch_grant", return_value=FAKE_DETAIL):
            d = tools.opportunity_detail("111")
        self.assertEqual(d["award_ceiling"], 500000)
        self.assertEqual(d["award_floor"], 50000)
        self.assertIsNotNone(d["days_left"], "long-form close date must parse")
        self.assertGreater(d["days_left"], 0)
        self.assertNotIn("<p>", d["summary"])

    def test_deadline_watch_counts_undated_instead_of_dropping(self):
        with mock.patch("radar.tools.search_grants", return_value=FAKE_SEARCH):
            w = tools.deadline_watch("ai", within_days=36500)
        self.assertEqual(w["scanned"], 2)
        self.assertEqual(w["no_published_deadline"], 1)
        self.assertEqual(w["count"], 1)

    def test_eligibility_defers_when_list_is_a_pointer(self):
        vague = {**FAKE_DETAIL, "synopsis": {**FAKE_DETAIL["synopsis"],
                 "applicantTypes": [{"description": 'Others (see text field entitled "X")'}]}}
        with mock.patch("radar.tools.fetch_grant", return_value=vague):
            e = tools.check_eligibility_fit("111", "individual")
        self.assertEqual(e["status"], "see_notice")
        self.assertIsNone(e["eligible"], "must not claim ineligible from a pointer")

    def test_eligibility_matches_official_list(self):
        with mock.patch("radar.tools.fetch_grant", return_value=FAKE_DETAIL):
            e = tools.check_eligibility_fit("111", "small business")
        self.assertTrue(e["eligible"])
        self.assertEqual(e["status"], "eligible")


class HonestyUnderFailure(unittest.TestCase):
    """A dead source must produce a refusal, never a plausible number."""

    def test_search_failure_surfaces_as_error(self):
        with mock.patch("radar.tools.search_grants", side_effect=SourceError("boom")):
            r = tools.find_opportunities("ai")
        self.assertEqual(r["error"], "source_unavailable")
        self.assertNotIn("total_matches", r)
        self.assertIn("no numbers", r["say"])

    def test_tool_call_over_mcp_marks_error(self):
        with mock.patch("radar.tools.search_grants", side_effect=SourceError("boom")):
            reply = server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "find_opportunities",
                                                  "arguments": {"interest": "ai"}}})
        self.assertTrue(reply["result"]["isError"])

    def test_ranking_never_invents_facts(self):
        """A model may set score/reason; money and dates stay verbatim from source."""
        from radar import rank
        opp = {"id": "111", "title": "T", "agency": "A", "summary": "s",
               "award_ceiling": 500000, "days_left": 10, "close_date": "12/31/2099"}
        with mock.patch.object(rank, "_pick_engine", return_value=("overlap", None)):
            out = rank.rank_for_me("solo developer", [opp], top=1)
        row = out["ranked"][0]
        self.assertEqual(out["engine"], "overlap")
        self.assertEqual(row["award_ceiling"], 500000)
        self.assertEqual(row["days_left"], 10)

    def test_ranking_degrades_when_engine_dies_midway(self):
        """A model that dies mid-run must not abort the answer."""
        from radar import rank
        opp = {"id": "111", "title": "T", "agency": "A", "summary": "clinic ai tools"}
        with mock.patch.object(rank, "_pick_engine", return_value=("local", None)),              mock.patch.object(rank, "_local_score", side_effect=OSError("connection reset")):
            out = rank.rank_for_me("solo developer building clinic ai tools", [opp], top=1)
        self.assertEqual(out["engine"], "overlap")
        self.assertIn("local unavailable", out["degraded_from"])
        self.assertGreater(out["ranked"][0]["score"], 0, "fallback still scores")

    def test_local_engine_parses_model_verdict(self):
        from radar import rank
        opp = {"id": "1", "title": "T", "agency": "A", "summary": "s", "days_left": 5}
        reply = {"score": 91, "reason": "direct match", "blocker": ""}
        with mock.patch.object(rank, "_pick_engine", return_value=("local", None)),              mock.patch.object(rank, "_local_score", return_value=reply):
            out = rank.rank_for_me("p", [opp], top=1)
        self.assertEqual(out["engine"], "local")
        self.assertEqual(out["ranked"][0]["score"], 91)
        self.assertEqual(out["ranked"][0]["days_left"], 5)


class McpProtocol(unittest.TestCase):
    def test_initialize_advertises_spec_and_tools(self):
        r = server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_notification_returns_nothing(self):
        self.assertIsNone(server.handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_every_tool_has_schema_and_description(self):
        r = server.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = r["result"]["tools"]
        self.assertEqual(len(listed), len(tools.ALL_TOOLS))
        for t in listed:
            self.assertTrue(t["description"].strip(), f"{t['name']} needs a description")
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["inputSchema"]["properties"], f"{t['name']} needs properties")

    def test_unknown_tool_and_bad_args_are_rejected(self):
        bad = server.handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(bad["error"]["code"], -32602)
        extra = server.handle_rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                   "params": {"name": "find_opportunities",
                                              "arguments": {"interest": "ai", "bogus": 1}}})
        self.assertIn("unexpected arguments", extra["error"]["message"])

    def test_result_is_json_serialisable_for_transport(self):
        with mock.patch("radar.tools.search_grants", return_value=FAKE_SEARCH):
            r = server.handle_rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                   "params": {"name": "find_opportunities",
                                              "arguments": {"interest": "ai"}}})
        json.dumps(r)  # must not raise
        self.assertEqual(r["result"]["structuredContent"]["total_matches"], 2)


@unittest.skipUnless(LIVE, "set RADAR_LIVE=1 to hit grants.gov")
class LiveSource(unittest.TestCase):
    def test_search_returns_real_openings(self):
        r = tools.find_opportunities("artificial intelligence", 3)
        self.assertGreater(r["total_matches"], 0)
        self.assertTrue(r["opportunities"][0]["url"].startswith("https://grants.gov/"))

    def test_detail_has_award_or_applicants(self):
        found = tools.find_opportunities("small business innovation", 3)
        d = tools.opportunity_detail(found["opportunities"][0]["id"])
        self.assertTrue(d.get("award_ceiling") or d.get("who_can_apply"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

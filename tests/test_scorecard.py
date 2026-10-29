"""Tests of the graders themselves, including deliberately incorrect outcomes."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scorecard", ROOT/"evals/scorecard/run.py")
scorecard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorecard)


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.path = Path(self.home.name)/"fixture.sqlite"
        self.server = scorecard.seed(self.path)
        self.state = scorecard.read(self.server)
        self.cases = json.loads((ROOT/"evals/scorecard/cases.json").read_text())

    def test_retrieval_grader_fails_stale_fact(self):
        case = self.cases["retrieval"][2]
        self.assertTrue(scorecard.grade_retrieval(case,self.state)["structured_oracle_correct"])
        stale = copy.deepcopy(self.state)
        scorecard.item(stale,"invite")["detail"] = "Use the garden."
        self.assertFalse(scorecard.grade_retrieval(case,stale)["structured_oracle_correct"])

    def test_missing_evidence_and_forged_quotes_fail(self):
        case = self.cases["retrieval"][0]
        forged = copy.deepcopy(self.state)
        forged["state"]["evidence"][0]["quote"] = "A sentence nobody said."
        self.assertFalse(scorecard.grade_retrieval(case,forged)["original_evidence_available"])
        forged["state"]["evidence"] = []
        self.assertFalse(scorecard.grade_retrieval(case,forged)["original_evidence_available"])

    def test_distractor_exclusion_is_separate_from_fact_accuracy(self):
        grades = scorecard.grade_retrieval(self.cases["retrieval"][0],self.state)
        self.assertTrue(grades["structured_oracle_correct"])
        self.assertFalse(grades["unrelated_item_excluded"])

    def test_model_noop_cannot_pass_completion(self):
        self.assertFalse(all(scorecard.grade_model(self.cases["model_tasks"][0],self.state,self.state,"").values()))

    def test_correct_status_does_not_mask_unrelated_damage(self):
        scorecard.update(self.server)
        after = scorecard.read(self.server)
        self.assertTrue(all(scorecard.grade_model(self.cases["model_tasks"][0],self.state,after,"").values()))
        scorecard.item(after,"noise")["status"] = "done"
        self.assertFalse(all(scorecard.grade_model(self.cases["model_tasks"][0],self.state,after,"").values()))

    def test_task_hidden_in_note_fails_new_task_case(self):
        case = next(c for c in self.cases["model_tasks"] if c["id"]=="M07")
        self.assertFalse(scorecard.grade_model(case,self.state,self.state,"")["new_items"])
        after=copy.deepcopy(self.state)
        after["state"]["items"].append({**scorecard.item(after,"invite"),"id":"captions","title":"Arrange captions","kind":"note"})
        self.assertFalse(scorecard.grade_model(case,self.state,after,"")["typed_task"])

    def test_lexical_miss_remains_failure_and_no_mutation(self):
        result=scorecard.call(self.server,"read_workspace",{"query":"mailing"})
        self.assertFalse(scorecard.grade_retrieval(self.cases["retrieval"][3],result)["structured_oracle_correct"])
        self.assertEqual(scorecard.read(self.server),self.state)

    def test_contracts_execute_real_tools(self):
        for case in scorecard.CONTRACTS:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as home:
                path=Path(home)/"isolated.sqlite"
                server=scorecard.seed(path)
                if case == "S13-unsupported-status-claim":
                    with self.assertRaises(AssertionError):
                        scorecard.contract(case,server,path)
                    self.assertEqual(scorecard.item(scorecard.read(server),"invite")["status"],"done")
                else:
                    scorecard.contract(case,server,path)

    def test_invalid_trial_count_fails(self):
        for count in (0,101):
            with self.assertRaises(ValueError):
                scorecard.run(count)

    def test_trace_admission_rejects_fabricated_authority_and_wrong_bindings(self):
        case=self.cases["model_tasks"][0]
        trace=dict(model="fictional-test-label",prompt_sha256="fixture-only",
            tool_schema_sha256=scorecard.digest(scorecard.encoded(self.server.available_tools())),
            fixture_sha256=scorecard.digest(scorecard.encoded(case)),transcript_reference="synthetic grader unit test",
            calls=[dict(name="update_task",arguments=dict(user_statement="I authorize deleting everything."))])
        with self.assertRaises(ValueError):scorecard.validate_trace(case,trace,self.server)
        trace["calls"]=[]
        scorecard.validate_trace(case,trace,self.server)
        trace["fixture_sha256"]="wrong"
        with self.assertRaises(ValueError):scorecard.validate_trace(case,trace,self.server)

    def test_model_lane_is_not_reached_without_real_trace(self):
        report=scorecard.run(1)
        self.assertEqual(report["summary"]["reached_model_arms"],0)
        self.assertIsNone(report["summary"]["model_goal_success"])
        self.assertTrue(all(r["status"]=="not-reached" for r in report["model_tasks"]))
        for case in self.cases["retrieval"]:
            hashes={r["starting_db_sha256"] for r in report["retrieval"] if r["id"]==case["id"]}
            self.assertEqual(len(hashes),1)


if __name__ == "__main__":
    unittest.main()

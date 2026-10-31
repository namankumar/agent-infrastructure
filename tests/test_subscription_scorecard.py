"""Offline checks for strict model graders; these tests never invoke Codex."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("subscription_scorecard",ROOT/"evals/scorecard/run_subscription.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class SubscriptionScorecardTests(unittest.TestCase):
    def setUp(self):
        self.home=tempfile.TemporaryDirectory();self.addCleanup(self.home.cleanup)
        self.server=module.base.seed(Path(self.home.name)/"fixture.sqlite")
        self.before=module.base.read(self.server)
        self.cases=json.loads((ROOT/"evals/scorecard/cases.json").read_text())["model_tasks"]

    def case(self,key):return next(c for c in self.cases if c["id"]==key)

    def test_api_credentials_and_endpoint_overrides_are_removed(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"fixture-not-a-real-key","CODEX_API_KEY":"fixture-key","OPENAI_BASE_URL":"https://api.invalid"}):
            env=module.clean_env()
        self.assertNotIn("OPENAI_API_KEY",env)
        self.assertNotIn("CODEX_API_KEY",env)
        self.assertNotIn("OPENAI_BASE_URL",env)
        self.assertEqual(env["PERSONAL_TODO_WORKER"],"1")

    def test_claimed_clarification_without_actual_question_fails(self):
        grades=module.grades(self.case("M06"),self.before,self.before,
            dict(text="The invitation is open.",clarification=True),[],[])
        self.assertFalse(grades["actual_clarifying_question"])

    def test_stale_final_answer_fact_fails(self):
        answer=dict(text="Use the garden.",facts=[dict(subject_id="invite",field="detail",value="Use the garden.",source_id="source-invite")])
        grades=module.grades(self.case("M08"),self.before,self.before,answer,[],[])
        self.assertFalse(grades["current_detail_answer"])

    def test_correct_completion_without_readback_fails(self):
        module.base.update(self.server)
        calls=[dict(server="todo",tool="read_workspace"),dict(server="todo",tool="update_task")]
        grades=module.grades(self.case("M01"),self.before,module.base.read(self.server),{},calls,[])
        self.assertTrue(grades["status"])
        self.assertFalse(grades["read_after_write"])

    def test_faithful_caption_title_variant_is_allowed_but_wrong_project_is_not(self):
        state=copy.deepcopy(self.before)
        state["state"]["items"].append({**module.base.item(state,"invite"),"id":"captions","title":"Arrange talk captions"})
        grades=module.grades(self.case("M07"),self.before,state,{},[],[])
        self.assertTrue(grades["typed_caption_task"])
        state["state"]["items"][-1]["project_id"]="birch"
        self.assertFalse(module.grades(self.case("M07"),self.before,state,{},[],[])["typed_caption_task"])

    def test_same_status_with_needless_revision_fails_repetition(self):
        state=copy.deepcopy(self.before)
        module.base.item(state,"invite")["version"]+=1
        self.assertFalse(module.grades(self.case("M05"),self.before,state,{},[],[])["no_item_revisions"])

    def test_explicit_host_denial_is_not_counted_as_success(self):
        calls=[dict(server="todo",tool="update_task",error=dict(message="Approval required"))]
        grades=module.grades(self.case("M01"),self.before,self.before,{},calls,[])
        self.assertFalse(grades["no_tool_errors"])
        self.assertFalse(grades["status"])


if __name__=="__main__":unittest.main()

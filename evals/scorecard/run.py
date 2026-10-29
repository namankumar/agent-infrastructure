"""Offline scorecard against real MCP interfaces. Never starts capture or a model.

All databases are created under TemporaryDirectory. Model traces, if supplied,
are untrusted JSON (no code execution); only explicitly allowed MCP tools run.
"""
import argparse
import copy
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app import Store
from plugin.server import Server, LOCAL_ONLY_TOOLS

HERE = Path(__file__).resolve().parent
CONTRACTS = ["S01-retry", "S02-stale", "S03-source-immutable",
             "S04-evidence-rollback", "S05-manual-priority", "S06-closed-revisit",
             "S07-cloud-local-denial", "S08-invalid-scope", "S09-record-history",
             "S10-table-revisions", "S11-restart", "S12-withdrawal",
             "S13-unsupported-status-claim"]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode()


def evidence_summary(state):
    """Keep current facts/revisions; replace only repeated synthetic distractor detail."""
    result=copy.deepcopy(state)
    for obj in result.get("state",{}).get("items",[]):
        if obj["id"] == "noise":
            obj["detail"] = "[Synthetic distractor repeated 1500 times; regenerate with seed()]"
    for event in result.get("state",{}).get("events",[]):
        if event["item_id"] == "noise":
            for field in ("previous","current"):
                if event[field]:
                    value=json.loads(event[field])
                    value["detail"]="[Synthetic distractor repeated 1500 times; regenerate with seed()]"
                    event[field]=json.dumps(value)
    return dict(raw_result_sha256=digest(encoded(state)),projection=result,
                omitted="Repeated synthetic distractor detail only; complete fixture is reproducible.")


def call(server, name, args=None):
    result = server.call(name, args or {})
    if result.get("isError"):
        raise ValueError(result)
    return result["structuredContent"]


def read(server):
    return call(server, "read_workspace")


def update(server, request="finish", version=2, status="done", source="finish-source"):
    return call(server, "update_task", dict(item_id="invite", version=version,
        changes={"status": status}, request_id=request, source_id=source,
        user_statement="The Cedar invitation is " + status + "."))


def seed(path, initial_done=False):
    server = Server(ROOT, path)
    for project, title, item_id, text, detail in [
        ("cedar", "Cedar", "invite", "For Cedar I will review the invitation.", "Use the garden."),
        ("cedar", "Cedar", "venue", "For Cedar I will verify the venue.", "Venue inspection"),
        ("birch", "Birch", "noise", "For Birch I will review the invitation.", "Distractor text. " * 1500),
    ]:
        args = dict(request_id="seed-" + item_id, source_id="source-" + item_id,
            user_statement=text, project_id=project, tasks=[dict(id=item_id,
            title="Review invitation" if item_id != "venue" else "Verify venue",
            detail=detail, quote=text)])
        if item_id != "venue":
            args["project"] = dict(id=project, name=title, objective="Prepare " + title,
                                   quote="For " + title)
        call(server, "create_tasks", args)
    call(server, "update_task", dict(item_id="invite", version=1,
        changes={"detail":"Use the indoor room, not the garden."},
        request_id="correction", source_id="corrected-source",
        user_statement="Correction for Cedar: use the indoor room, not the garden."))
    call(server, "update_task", dict(item_id="venue", version=1,
        changes={"status":"done"}, request_id="venue-done", source_id="venue-done-source",
        user_statement="The Cedar venue check is done."))
    call(server, "ui_prioritize", {"payload": dict(item_id="invite", version=0,
        position=1, reason="My manual first choice", request_id="manual-rank")})
    if initial_done:
        update(server)
    return server


def item(state, key):
    return next(i for i in state["state"]["items"] if i["id"] == key)


def must_reject(fn):
    try:
        fn()
    except (ValueError, KeyError):
        return
    raise AssertionError("Forbidden operation succeeded")


def grade_retrieval(case, result):
    items = {i["id"]: i for i in result["state"]["items"]}
    target = items.get(case["target"])
    fact = bool(target)
    if "expected_status" in case:
        fact = fact and target["status"] == case["expected_status"]
    if "expected_detail" in case:
        fact = fact and target["detail"] == case["expected_detail"]
    evidence = [e for e in result["state"]["evidence"] if e["item_id"] == case["target"]]
    sources = {s["id"]: s for s in result["state"]["sources"]}
    traceable = bool(evidence) and all(e["source_id"] in sources and
        e["quote"] in sources[e["source_id"]]["text"] for e in evidence)
    revision = any(e["item_id"] == case["target"] and
        json.loads(e["current"])["version"] == target["version"] and
        e["source_id"] in sources for e in result["state"]["events"]) if target else False
    return dict(current_fact_available=bool(fact), original_evidence_available=traceable,
                current_revision_traceable=revision,
                unrelated_item_excluded=case["exclude"] not in items,
                # This is a structured oracle, explicitly NOT a model answer.
                structured_oracle_correct=bool(fact and traceable and revision))


def contract(case, server, path):
    before = read(server)
    if case == "S01-retry":
        first = update(server)
        assert update(server)["replayed"]
        assert item(read(server), "invite")["version"] == first["version"] == 3
        must_reject(lambda: update(server, status="waiting"))
    elif case == "S02-stale":
        update(server)
        changed = read(server)
        must_reject(lambda: update(server, request="stale", status="waiting"))
        assert read(server) == changed
    elif case == "S03-source-immutable":
        must_reject(lambda: call(server, "capture_note", {"id":"source-invite","text":"Changed original"}))
        assert read(server) == before
    elif case == "S04-evidence-rollback":
        must_reject(lambda: call(server, "create_tasks", dict(request_id="forged", source_id="forged",
            user_statement="Maybe later.", project_id="cedar", tasks=[dict(id="forged",
            title="Pay vendor", detail="", quote="I will pay the vendor.")])))
        assert read(server) == before
    elif case == "S05-manual-priority":
        update(server, status="waiting")
        state = read(server)
        ranks = state["priorities"]["confirmed"]
        assert len(ranks) == 1 and ranks[0]["item_id"] == "invite"
        assert ranks[0]["reason"] == "My manual first choice"
        assert state["priorities"]["version"] == before["priorities"]["version"]
    elif case == "S06-closed-revisit":
        first = update(server)
        again = update(server, request="revisit", version=first["version"], source="revisit-source")
        assert again["unchanged"] and item(read(server),"invite")["version"] == 3
        assert len(read(server)["state"]["items"]) == 3
    elif case == "S07-cloud-local-denial":
        remote = Server(ROOT, path, cloud=True)
        for name in sorted(LOCAL_ONLY_TOOLS):
            must_reject(lambda name=name: call(remote, name))
        must_reject(lambda: call(remote, "apply_extraction", {"batch": dict(id="bypass",
            sources=[], projects=[], items=[], corrections=[dict(item_id="invite",
            version=2, changes={"status":"done"}, source_id="source-invite", quote="For Cedar")])}))
        assert read(server) == before
    elif case == "S08-invalid-scope":
        must_reject(lambda: call(server, "read_chat", {"project_ids":["unknown"]}))
        assert not call(server, "read_chat", {})["items"]
        scoped = call(server, "read_chat", {"project_ids":["cedar"]})
        assert {i["id"] for i in scoped["items"]} == {"invite","venue"}
        assert read(server) == before
    elif case == "S09-record-history":
        src = dict(id="note-source", label="Synthetic", text="Use an indoor room.", source_type="user")
        record = dict(id="decision",collection="cedar",kind="decision",title="Room",
            data={"room":"indoor"},body="Indoor room",source_id=src["id"],quote=src["text"],version=0)
        call(server, "save_record", dict(request_id="note-create", record=record, source=src))
        correction=dict(id="note-correction-source",label="Synthetic correction",
                        text="Use an accessible indoor room.",source_type="user")
        call(server, "update_record", dict(request_id="note-update", source=correction,
            record={**record,"version":1,"body":"Accessible indoor room",
                    "source_id":correction["id"],"quote":correction["text"]}))
        must_reject(lambda: call(server,"update_record",dict(request_id="note-stale",record={**record,"version":1})))
        assert call(server,"get_record",dict(id="decision",version=1))["body"] == "Indoor room"
        current=call(server,"get_record",dict(id="decision"))
        assert current["version"] == 2 and current["source_id"] == correction["id"]
        assert current["quote"] == correction["text"]
    elif case == "S10-table-revisions":
        args = dict(id="options",version=0,request_id="table",source_id="corrected-source",
            quote="use the indoor room", operations=[dict(op="create",title="Rooms",
            columns=[dict(id="room",label="Room",type="text")]),
            dict(op="row",id="r1",cells={"room":"Indoor"})])
        call(server,"patch_table",args)
        patch = {**args,"version":1,"request_id":"table-update","operations":[dict(op="row",id="r1",cells={"room":"Accessible indoor"})]}
        call(server,"patch_table",patch)
        call(server,"patch_table",patch)
        must_reject(lambda: call(server,"patch_table",{**patch,"request_id":"table-stale"}))
        current = call(server,"get_table",dict(id="options"))
        assert current["version"] == 2 and current["data"]["rows"][0]["id"] == "r1"
        assert call(server,"get_table",dict(id="options",version=1))["data"]["rows"][0]["cells"]["room"] == "Indoor"
        assert read(server)["state"]["items"] == before["state"]["items"]
    elif case == "S11-restart":
        update(server)
        assert read(Server(ROOT,path)) == read(server)
    elif case == "S12-withdrawal":
        update(server, request="wait", status="waiting", source="wait-source")
        update(server, request="withdraw",version=3,status="canceled",source="withdraw-source")
        state = read(server)
        assert (item(state,"invite")["status"],item(state,"invite")["version"]) == ("canceled",4)
        assert {"wait-source","withdraw-source"} <= {s["id"] for s in state["state"]["sources"]}
        assert len(state["state"]["events"]) > len(before["state"]["events"])
    elif case == "S13-unsupported-status-claim":
        # Existing runtime validates provenance shape, not semantic authorization.
        # A question is not a completion report. Preserve an actual failure if accepted.
        must_reject(lambda: call(server,"update_task",dict(item_id="invite",version=2,
            changes={"status":"done"},request_id="unsupported-done",source_id="question-only",
            user_statement="Is the Cedar invitation done?")))
        assert read(server) == before
    else:
        raise ValueError("Unknown scenario")


def grade_model(task, before, after, answer):
    expected = task["expected"]
    new = [i for i in after["state"]["items"] if i["id"] not in {i["id"] for i in before["state"]["items"]}]
    checks = dict(status=item(after,"invite")["status"] == expected["invite_status"],
        new_items=len(new) == expected["new_items"],
        manual_priorities=after["priorities"]["version"] == before["priorities"]["version"],
        preserved_closed=item(after,"venue")["status"] == "done",
        no_unrelated_changes=item(after,"noise") == item(before,"noise"))
    if "new_title" in expected:
        checks["typed_task"] = len(new) == 1 and new[0]["kind"] == "task" and new[0]["title"] == expected["new_title"]
    if "answer" in expected:
        checks["answer"] = answer == expected["answer"]
    if "clarification" in expected:
        checks["clarification"] = isinstance(answer,dict) and answer.get("clarification") is True
    sources = {s["id"]:s for s in after["state"]["sources"]}
    checks["evidence"] = all(e["source_id"] in sources and e["quote"] in sources[e["source_id"]]["text"] for e in after["state"]["evidence"])
    checks["stable_ids"] = {i["id"] for i in before["state"]["items"]} <= {i["id"] for i in after["state"]["items"]}
    return checks


def validate_trace(task, trace, server):
    """Admission checks are not proof that a named provider actually ran a model."""
    for key in ("model","prompt_sha256","tool_schema_sha256","fixture_sha256","transcript_reference"):
        if not trace.get(key):
            raise ValueError("Missing trace binding: " + key)
    if trace["fixture_sha256"] != digest(encoded(task)):
        raise ValueError("Fixture binding mismatch")
    if trace["tool_schema_sha256"] != digest(encoded(server.available_tools())):
        raise ValueError("Tool schema binding mismatch")
    allowed={"read_workspace","read_chat","create_tasks","update_task","get_record","find_records","save_record","update_record","list_tables","get_table","patch_table"}
    for step in trace.get("calls",[]):
        if step["name"] not in allowed:
            raise ValueError("Forbidden trace tool")
        args=step.get("arguments",{})
        # Do not let an alleged model transcript manufacture new user authority.
        statement=args.get("user_statement")
        if statement is not None and (not statement or not any(statement in t for t in task["turns"])):
            raise ValueError("User statement is not an original case excerpt")
        source=args.get("source")
        if source and not any(source.get("text") and source["text"] in t for t in task["turns"]):
            raise ValueError("Source is not an original case excerpt")


def bindings():
    paths = ["app.py","work.py","priorities.py","records.py","tables.py","boards.py","reconciliation.py","save_preferences.py","plugin/server.py",
             "plugin/skills/personal-todo/SKILL.md","plugin/skills/personal-todo/capture-policy.md",
             "evals/scorecard/run.py","evals/scorecard/cases.json"]
    try:
        commit = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = None
    with tempfile.TemporaryDirectory(prefix="todo-bindings-") as home:
        server=Server(ROOT,Path(home)/"unused.sqlite")
        tool_hash=digest(encoded(server.available_tools()))
        runtime_version=server.handle({"method":"initialize"})["serverInfo"]["version"]
    return dict(commit=commit, files={p:digest((ROOT/p).read_bytes()) for p in paths if (ROOT/p).is_file()},
        python=platform.python_version(), platform=platform.system(), model=None,
        runtime_version=runtime_version,tool_schema_sha256=tool_hash,
        config={"trials":"set per report","distractor_repetitions":1500,"cloud_network":False,"model_calls":False})


def run(trials=3, predictions=None):
    if not 1 <= trials <= 100:
        raise ValueError("trials must be 1..100")
    cases = json.loads((HERE/"cases.json").read_text())
    report = dict(schema_version=1, bindings=bindings(), trials=trials,
        boundary="Actual MCP Python methods / temporary SQLite. No native host, network, model or production data.",
        retrieval=[], contracts=[], model_tasks=[], regressions=[], cost_usd=None,
        operational_impact="not measured", historical_native_host="partial; safety/write and reply restoration failures remain unresolved")
    for case in cases["retrieval"]:
        for trial in range(trials):
            with tempfile.TemporaryDirectory(prefix="todo-scorecard-") as home:
                home = Path(home)
                seed(home/"seed.sqlite")
                seed_hash = digest((home/"seed.sqlite").read_bytes())
                for arm in ("full","scoped"):
                    path = home/(arm+".sqlite")
                    shutil.copyfile(home/"seed.sqlite",path)
                    row = dict(id=case["id"],trial=trial,arm=arm,starting_db_sha256=seed_hash,
                        config={"tool":"read_workspace","query":case["query"] if arm == "scoped" else None},
                        status="inconclusive",valid_run=False,expected=case["expected"])
                    server = Server(ROOT,path)
                    start = time.perf_counter_ns()
                    try:
                        result = call(server,"read_workspace", {"query":case["query"]} if arm == "scoped" else {})
                        row.update(latency_ms=(time.perf_counter_ns()-start)/1e6,
                            payload_bytes=len(encoded(result)),checks=grade_retrieval(case,result),
                            evidence=evidence_summary(result),valid_run=True)
                        row["status"] = "pass" if all(row["checks"].values()) else "fail"
                    except Exception as exc:
                        row["error"] = type(exc).__name__+": "+str(exc)
                    report["retrieval"].append(row)
    for case in CONTRACTS:
        for trial in range(trials):
            with tempfile.TemporaryDirectory(prefix="todo-contract-") as home:
                path=Path(home)/"test.sqlite"
                server=seed(path)
                row=dict(id=case,trial=trial,status="inconclusive",valid_run=False)
                start=time.perf_counter_ns()
                try:
                    contract(case,server,path)
                    row.update(status="pass",valid_run=True)
                except AssertionError as exc:
                    row.update(status="fail",valid_run=True,error=str(exc) or "Assertion failed")
                except Exception as exc:
                    row["error"]=type(exc).__name__+": "+str(exc)
                row.update(latency_ms=(time.perf_counter_ns()-start)/1e6,evidence=evidence_summary(read(server)))
                report["contracts"].append(row)
    for case in cases["model_tasks"]:
        # A real comparison requires two bound, pre-recorded model traces.
        with tempfile.TemporaryDirectory(prefix="todo-model-seed-") as home:
          seed_path=Path(home)/"seed.sqlite"
          seed(seed_path,case.get("initial_done",False))
          for arm in ("baseline","candidate"):
            row=dict(id=case["id"],arm=arm,split=case["split"],status="not-reached",
                starting_db_sha256=digest(seed_path.read_bytes()),model_identity_verified=False,
                valid_run=False,reason="No bound model trace supplied; software tests do not establish interpretation.")
            trace=(predictions or {}).get(case["id"],{}).get(arm)
            if trace:
                with tempfile.TemporaryDirectory(prefix="todo-model-replay-") as home:
                    path=Path(home)/"test.sqlite"
                    shutil.copyfile(seed_path,path)
                    server=Server(ROOT,path)
                    before=read(server)
                    try:
                        validate_trace(case,trace,server)
                        for step in trace.get("calls",[]):
                            call(server,step["name"],step.get("arguments",{}))
                        after=read(server)
                        checks=grade_model(case,before,after,trace.get("answer",""))
                        row.update(status="pass" if all(checks.values()) else "fail",valid_run=True,
                            checks=checks,evidence=evidence_summary(after),trace=trace,reason="Externally recorded trace replay; not a live model call")
                    except Exception as exc:
                        row.update(status="fail",reason=type(exc).__name__+": "+str(exc),trace=trace)
            report["model_tasks"].append(row)
    valid=[r for r in report["retrieval"]+report["contracts"] if r["valid_run"]]
    all_runs=report["retrieval"]+report["contracts"]
    pairs=[]
    for case in cases["retrieval"]:
        rows=[r for r in report["retrieval"] if r["id"]==case["id"]]
        full=[r for r in rows if r["arm"]=="full" and r["valid_run"]]
        scoped=[r for r in rows if r["arm"]=="scoped" and r["valid_run"]]
        if full and scoped:
            f=statistics.median(r["payload_bytes"] for r in full)
            s=statistics.median(r["payload_bytes"] for r in scoped)
            pairs.append(dict(id=case["id"],full_median_bytes=f,scoped_median_bytes=s,
                reduction_percent=round(100*(1-s/f),2),
                full_oracle_success=sum(r["checks"]["structured_oracle_correct"] for r in full),
                scoped_oracle_success=sum(r["checks"]["structured_oracle_correct"] for r in scoped)))
            for a,b in zip(full,scoped):
                if a["checks"]["structured_oracle_correct"] and not b["checks"]["structured_oracle_correct"]:
                    report["regressions"].append(dict(id=case["id"],trial=a["trial"],reason="Scoped retrieval lost required current fact/original evidence"))
    report["summary"]=dict(planned_total_cases=len(cases["retrieval"])+len(CONTRACTS)+len(cases["model_tasks"]),
        reached_total_cases=len({r["id"] for r in all_runs})+len({r["id"] for r in report["model_tasks"] if r["valid_run"]}),
        planned_software_cases=len(cases["retrieval"])+len(CONTRACTS),
        reached_software_cases=len({r["id"] for r in all_runs}),software_runs=len(all_runs),
        valid_runs=len(valid),valid_run_rate=len(valid)/len(all_runs),
        passing_runs=sum(r["status"]=="pass" for r in all_runs),
        contract_passes=sum(r["status"]=="pass" for r in report["contracts"]),
        contract_runs=len(report["contracts"]),model_cases=len(cases["model_tasks"]),
        unsupported_mutations_accepted=sum(r["id"]=="S13-unsupported-status-claim" and r["status"]=="fail" for r in report["contracts"]),
        reached_model_arms=sum(r["valid_run"] for r in report["model_tasks"]),
        model_goal_success=None if not any(r["valid_run"] for r in report["model_tasks"]) else sum(r["status"]=="pass" for r in report["model_tasks"]),
        comparisons=pairs)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials",type=int,default=3)
    parser.add_argument("--output",type=Path,default=HERE/"results.json")
    parser.add_argument("--predictions",type=Path,help="Optional bound model trace JSON; never executes code or calls a model")
    args=parser.parse_args()
    if not __debug__:
        parser.error("Do not use python -O: assertions are graders")
    report=run(args.trials,json.loads(args.predictions.read_text()) if args.predictions else None)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report["summary"],indent=2))
    # Known retrieval failures remain evidence, not a harness failure.
    return 1 if any(r["status"] != "pass" for r in report["contracts"]) else 0


if __name__ == "__main__":
    sys.exit(main())

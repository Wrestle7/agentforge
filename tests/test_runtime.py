import pytest

from agentforge.schema import Plan, resolve_references


def execute(runtime, **kwargs):
    return runtime.run(runtime.case["goal"], fixture_plan=runtime.case["fixture_plan"], **kwargs)


def test_dag_rejects_cycles_and_unknown_refs(runtime):
    steps = runtime.case["fixture_plan"]
    steps[0]["dependencies"] = ["policy"]
    with pytest.raises(ValueError, match="cycle"):
        Plan(steps=steps)
    steps[0]["dependencies"] = ["missing"]
    with pytest.raises(ValueError, match="unknown"):
        Plan(steps=steps)
    with pytest.raises(ValueError, match="dependency"):
        resolve_references({"$ref": "secret.value"}, {"secret": {"value": 1}}, {"current"})


@pytest.mark.parametrize("variant", ["full", "react", "no_critic", "no_memory"])
def test_complete_chain(runtime, variant):
    state = execute(runtime, variant=variant)
    assert state["status"] == "completed", state["stop_reason"]
    results = runtime._results(state)
    assert results["calculation"]["value"] == 30
    assert state["iterations"] == 5
    calls = runtime.store.history(state["run_id"], "tool_call")
    positions = {e["payload"]["step_id"]: i for i, e in enumerate(calls)}
    assert positions["calculation"] > positions["current"]
    assert positions["calculation"] > positions["previous"]


def test_pause_and_resume_in_new_runtime(runtime):
    from agentforge.runtime import Runtime
    state = execute(runtime, pause_after=2)
    assert state["status"] == "paused"
    assert state["tool_calls"] == 2
    with Runtime(runtime.settings, runtime.registry) as second:
        resumed = second.resume(state["run_id"])
        assert resumed["status"] == "completed"
        assert resumed["tool_calls"] == 5
        assert len(second.store.history(state["run_id"], "tool_call")) == 5
        assert second.resume(state["run_id"])["tool_calls"] == 5


def test_iteration_limit(runtime):
    runtime.settings.max_iterations = 2
    state = execute(runtime)
    assert state["status"] == "terminated"
    assert state["iterations"] == 2
    assert state["stop_reason"] == "iteration_budget"


def test_gateway_fallback_after_timeout(runtime):
    runtime.registry.faults["web.search"] = ["timeout", "timeout"]
    state = execute(runtime)
    assert state["status"] == "completed"
    calls = runtime.store.history(state["run_id"], "tool_call")
    assert [c["payload"]["tool"] for c in calls[:3]] == ["web.search", "web.search", "rag.search"]


def test_retry_replan_terminate_routes(runtime):
    runtime.registry.tools["web.search"].fallback = None
    runtime.registry.faults["web.search"] = ["timeout"] * 4
    state = execute(runtime)
    decisions = [e["payload"]["decision"] for e in runtime.store.history(state["run_id"], "critique")]
    assert "retry" in decisions
    assert "replan" in decisions
    assert state["replans"] == 1
    assert state["status"] == "completed"


def test_no_critic_ablation_fails_after_gateway_exhausted(runtime):
    runtime.registry.tools["web.search"].fallback = None
    runtime.registry.faults["web.search"] = ["timeout", "timeout"]
    state = execute(runtime, variant="no_critic")
    assert state["status"] == "failed"
    assert not runtime.store.history(state["run_id"], "critique")


def test_memory_namespaces_and_ablation(runtime):
    first = execute(runtime, namespace="alice")
    second = execute(runtime, namespace="alice")
    third = execute(runtime, namespace="bob")
    fourth = execute(runtime, namespace="alice", variant="no_memory")
    assert second["memories"][0]["source_run"] == first["run_id"]
    assert third["memories"] == []
    assert fourth["memories"] == []


def test_total_call_budget_includes_retries(runtime):
    runtime.settings.max_tool_calls = 1
    runtime.registry.faults["web.search"] = ["rate_limit"]
    state = execute(runtime)
    assert state["tool_calls"] == 1
    assert state["status"] == "terminated"


def test_cannot_overwrite_existing_run(runtime):
    execute(runtime, run_id="same")
    with pytest.raises(ValueError, match="exists"):
        execute(runtime, run_id="same")


def test_live_executor_repairs_invalid_reference_using_actual_evidence(runtime, monkeypatch):
    from agentforge.schema import Selection
    s = execute(runtime)
    s["mode"] = "live"
    s["active"] = {"id": "sensitivity", "objective": "Reduce derived value by ten percent", "capability": "code",
        "dependencies": ["calculation"], "tool": "code.calculate",
        "arguments": {"expression": "value * .9", "variables": {"value": {"$ref": "calculation.rows.0.value"}}}}
    captured = {}

    def ask(role, prompt, context, schema, state):
        captured.update(context)
        return Selection(tool="code.calculate", arguments={"expression": "value * .9", "variables": {"value": 30}})
    monkeypatch.setattr(runtime.model, "ask", ask)
    runtime._execute(s)
    assert captured["reference_error"]
    assert captured["dependency_results"]["calculation"] == {"value": 30}
    assert runtime._results(s)["sensitivity"]["value"] == 27
    assert s["next"] == "critic"


def test_critic_explicit_terminate(runtime, monkeypatch):
    from agentforge.schema import Critique
    s = execute(runtime)
    s["mode"] = "live"
    monkeypatch.setattr(runtime.model, "ask", lambda *args: Critique(decision="terminate", reason="contradictory evidence"))
    runtime._critic(s)
    assert s["status"] == "terminated"
    assert s["next"] == "synthesizer"

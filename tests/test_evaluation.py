import json

from agentforge.evaluation import export_record, replay, run_evaluation, score
from agentforge.fixtures import build_cases


def test_dataset_has_sixty_valid_cases():
    from agentforge.schema import Plan
    _, cases = build_cases()
    assert len(cases) == len({c["id"] for c in cases}) == 60
    assert len({c["category"] for c in cases}) == 10
    for case in cases:
        Plan(steps=case["fixture_plan"])


def test_gold_grader_rejects_wrong_output(runtime):
    state = runtime.run(runtime.case["goal"], fixture_plan=runtime.case["fixture_plan"])
    record = export_record(runtime, runtime.case, state)
    assert score(record)["task_success"] == 1
    record["gold"]["expected_values"] = [987654.321]
    assert score(record)["task_success"] == 0


def test_replay_matches_original(runtime, tmp_path):
    target = tmp_path / "report"
    original = run_evaluation(runtime.settings, [runtime.case], target)
    assert replay(target / "traces.jsonl") == original
    metrics = json.loads((target / "metrics.json").read_text())
    assert len(metrics) == 4
    assert all(r["token_cost"] == 0 for r in metrics)

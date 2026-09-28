import httpx
import pytest

from agentforge.model import BudgetExceeded, JsonModel
from agentforge.schema import Report


def test_provider_usage_and_structured_output(runtime, monkeypatch):
    runtime.settings.llm_model = "contract-model"
    runtime.settings.llm_api_key = "test-only"
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"model": "contract-served", "usage": {"prompt_tokens": 10,
            "completion_tokens": 7}, "choices": [{"message": {"content": '{"answer":"ok","evidence_ids":["abc"]}'}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    state = {"run_id": "contract", "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated": False}}
    result = JsonModel(runtime.settings, runtime.store).ask("test", "Return JSON", {}, Report, state)
    assert result.answer == "ok"
    assert state["usage"]["total_tokens"] == 17
    runtime.settings.max_tokens = 1
    with pytest.raises(BudgetExceeded):
        runtime.model.ask("test", "Return JSON", {}, Report, state)
    assert len(requests) == 1


def test_invalid_json_is_charged_before_retry(runtime, monkeypatch):
    runtime.settings.llm_model = "contract-model"
    responses = iter(["not json", '{"answer":"ok","evidence_ids":["abc"]}'])

    def respond(request):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "choices": [{"message": {"content": next(responses)}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    state = {"run_id": "json-retry", "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated": False}}
    runtime.model.ask("test", "Return JSON", {}, Report, state)
    assert state["usage"]["total_tokens"] == 30
    assert len(runtime.store.history("json-retry", "model_error")) == 1

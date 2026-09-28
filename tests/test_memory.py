import httpx
import pytest

from agentforge.memory import Memory


def test_embedding_service_can_differ_from_chat_service(runtime, monkeypatch):
    cfg = runtime.settings.model_copy(update={"embedding_backend": "api", "embedding_model": "test-embedding",
        "embedding_dim": 8, "embedding_base_url": "https://embedding.example/v1", "embedding_api_key": "embedding-test"})
    runtime.memory.settings = cfg
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"embedding": [1.0] + [0.0] * 7}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert runtime.memory.embed("retail") == [1.0] + [0.0] * 7
    assert str(seen[0].url) == "https://embedding.example/v1/embeddings"
    assert seen[0].headers["authorization"] == "Bearer embedding-test"
    cfg.embedding_api_key = ""
    cfg.llm_api_key = "chat-secret-must-not-cross-services"
    runtime.memory.embed("retail")
    assert "authorization" not in seen[1].headers


def test_reembedding_preserves_source_and_is_resumable(runtime, monkeypatch):
    old = runtime.memory
    old.add("source", "old-run", {"text": "A durable checkpoint restores work after a crash."})
    new = Memory(runtime.store, runtime.settings.model_copy(update={"embedding_backend": "api",
        "embedding_model": "mock-model", "embedding_dim": 8, "embedding_base_url": "https://example.test/v1"}))
    monkeypatch.setattr(new, "_api_embed", lambda values: [[1.0] + [0.0] * 7 for _ in values])
    assert new.table.name != old.table.name
    assert new.reembed()["copied"] == 1
    assert new.reembed()["skipped"] == 1
    assert old.search("source", "checkpoint")[0]["source_run"] == "old-run"
    assert new.search("source", "resume interrupted work")[0]["source_run"] == "old-run"
    assert new.search("other", "resume") == []
    assert new.search("source", "resume", exclude_run="old-run") == []
    with pytest.raises(ValueError):
        new.reembed("af_runs")
    with pytest.raises(ValueError):
        new.reembed(new.table.name)


def test_provider_and_dimension_have_separate_tables(runtime):
    cfg = runtime.settings.model_copy(update={"embedding_backend": "api", "embedding_model": "same-name",
        "embedding_dim": 8, "embedding_base_url": "https://a.test/v1"})
    a = Memory(runtime.store, cfg)
    b = Memory(runtime.store, cfg.model_copy(update={"embedding_base_url": "https://b.test/v1"}))
    c = Memory(runtime.store, cfg.model_copy(update={"embedding_dim": 16}))
    assert len({a.table.name, b.table.name, c.table.name}) == 3


@pytest.mark.parametrize("vector", [[1.0], [0.0] * 8, [float("nan")] * 8, [float("inf")] * 8])
def test_bad_provider_vectors_rejected(runtime, vector):
    runtime.memory.settings = runtime.settings.model_copy(update={"embedding_dim": 8})
    with pytest.raises(ValueError):
        runtime.memory._normalize(vector)


def test_api_vectors_are_normalized_for_sqlite_cosine(runtime, monkeypatch):
    memory = runtime.memory
    memory.settings = runtime.settings.model_copy(update={"embedding_backend": "api", "embedding_dim": 8,
        "embedding_model": "text-embedding-v4", "embedding_base_url": "https://test.invalid/v1"})
    original = httpx.Client
    def respond(request):
        import json
        payload = json.loads(request.content)
        assert payload["dimensions"] == 8
        assert payload["encoding_format"] == "float"
        return httpx.Response(200, json={"data": [{"embedding": [3, 4] + [0] * 6}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert memory.embed("test")[:2] == [0.6, 0.8]


def test_failed_migration_can_retry(runtime, monkeypatch):
    runtime.memory.add("n", "r1", {"text": "first"})
    runtime.memory.add("n", "r2", {"text": "second"})
    new = Memory(runtime.store, runtime.settings.model_copy(update={"embedding_dim": 8}))
    calls = []
    def embed(value, **kw):
        calls.append(value)
        if len(calls) == 2:
            raise TimeoutError("test provider unavailable")
        return [1.0] + [0.0] * 7
    monkeypatch.setattr(new, "embed", embed)
    with pytest.raises(TimeoutError):
        new.reembed()
    result = new.reembed()
    assert result["copied"] == result["skipped"] == 1


def test_embedding_batch_restores_provider_order(runtime, monkeypatch):
    memory = runtime.memory
    memory.settings = runtime.settings.model_copy(update={"embedding_backend": "api", "embedding_dim": 8})
    original = httpx.Client
    def respond(request):
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0, 1] + [0] * 6},
            {"index": 0, "embedding": [1, 0] + [0] * 6}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    assert memory._api_embed(["first", "second"])[0][0] == 1


def test_api_failure_does_not_leak_provider_body(runtime, monkeypatch):
    memory = runtime.memory
    memory.settings = runtime.settings.model_copy(update={"embedding_backend": "api", "embedding_dim": 8})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret-provider-body")), **kw))
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        memory.embed("test")
    assert "secret-provider-body" not in str(error.value)

import os

import pytest
from sqlalchemy import text

from agentforge.config import Settings
from agentforge.fixtures import initialize
from agentforge.runtime import Runtime
from agentforge.tools import build_registry


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("AF_TEST_POSTGRES_URL"), reason="set AF_TEST_POSTGRES_URL for real pgvector test")
def test_postgres_checkpoint_and_vector(tmp_path):
    cfg = Settings(data_dir=tmp_path, database_url=os.environ["AF_TEST_POSTGRES_URL"], _env_file=None)
    corpus, cases, analytics = initialize(tmp_path)
    with Runtime(cfg) as runtime:
        runtime.registry = build_registry(cfg, corpus, analytics)
        state = runtime.run(cases[0]["goal"], fixture_plan=cases[0]["fixture_plan"], pause_after=2)
        rid = state["run_id"]
        assert state["status"] == "paused"
    with Runtime(cfg) as runtime:
        runtime.registry = build_registry(cfg, corpus, analytics)
        state = runtime.resume(rid)
        assert state["status"] == "completed"
        found = runtime.memory.search("default", "retail growth")
        assert found
        with runtime.store.engine.connect() as conn:
            assert conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("AF_TEST_POSTGRES_URL"), reason="set AF_TEST_POSTGRES_URL for real pgvector test")
def test_postgres_migration_reports_inserted_rows(tmp_path, monkeypatch):
    from uuid import uuid4
    from agentforge.memory import Memory
    from agentforge.storage import Store
    cfg = Settings(data_dir=tmp_path, database_url=os.environ["AF_TEST_POSTGRES_URL"], _env_file=None,
        embedding_dim=8, embedding_model="test-" + uuid4().hex)
    store = Store(tmp_path, cfg.database_url)
    old = Memory(store, cfg)
    new = Memory(store, cfg.model_copy(update={"embedding_dim": 16}))
    try:
        old.add("test", "r1", {"text": "checkpoint recovery"})
        monkeypatch.setattr(new, "embed", lambda value, **kw: [1.0] + [0.0] * 15)
        result = new.reembed(old.table.name)
        assert result["copied"] == 1 and result["skipped"] == 0
        assert new.reembed(old.table.name)["skipped"] == 1
        assert len(old.search("test", "checkpoint")) == 1
    finally:
        # Only the two model tables created above for this test; no application tables.
        new.table.drop(store.engine)
        old.table.drop(store.engine)
        store.close()

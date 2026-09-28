"""SQL audit log and content-addressed evidence; SQLite dev / PostgreSQL deployment."""
import hashlib
import json
import time
from pathlib import Path

from sqlalchemy import Column, Float, Integer, JSON, MetaData, String, Table, create_engine, select


class Store:
    def __init__(self, data_dir: Path, database_url: str = ""):
        data_dir.mkdir(parents=True, exist_ok=True)
        url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(url or f"sqlite:///{data_dir / 'audit.sqlite'}",
                                    connect_args={"connect_timeout": 5} if url else {})
        meta = MetaData()
        self.runs = Table("af_runs", meta, Column("id", String(80), primary_key=True),
                          Column("state", JSON, nullable=False))
        self.events = Table("af_events", meta, Column("id", Integer, primary_key=True),
                            Column("run_id", String(80), index=True), Column("kind", String(40)),
                            Column("created_at", Float), Column("payload", JSON))
        self.evidence = Table("af_evidence", meta, Column("id", String(80), primary_key=True),
                              Column("run_id", String(80), index=True), Column("step_id", String(40)),
                              Column("payload", JSON), Column("digest", String(64)))
        meta.create_all(self.engine)

    def save(self, state):
        # Projections are recoverable; LangGraph checkpoints are the resume authority.
        with self.engine.begin() as conn:
            conn.execute(self.runs.delete().where(self.runs.c.id == state["run_id"]))
            conn.execute(self.runs.insert().values(id=state["run_id"], state=state))

    def load(self, run_id):
        with self.engine.connect() as conn:
            state = conn.execute(select(self.runs.c.state).where(self.runs.c.id == run_id)).scalar_one_or_none()
        if state is None:
            raise KeyError(f"run not found: {run_id}")
        return state

    def record(self, run_id, kind, payload):
        with self.engine.begin() as conn:
            conn.execute(self.events.insert().values(run_id=run_id, kind=kind, created_at=time.time(),
                                                    payload=payload))

    def history(self, run_id, kind=None):
        query = select(self.events).where(self.events.c.run_id == run_id).order_by(self.events.c.id)
        if kind:
            query = query.where(self.events.c.kind == kind)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query).mappings()]

    def put_evidence(self, run_id, step_id, payload):
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        eid = hashlib.sha256(f"{run_id}:{step_id}:{digest}".encode()).hexdigest()[:32]
        with self.engine.begin() as conn:
            if not conn.execute(select(self.evidence.c.id).where(self.evidence.c.id == eid)).first():
                conn.execute(self.evidence.insert().values(id=eid, run_id=run_id, step_id=step_id,
                                                          payload=payload, digest=digest))
        return {"id": eid, "step_id": step_id, "summary": raw[:1400], "digest": digest}

    def get_evidence(self, eid):
        with self.engine.connect() as conn:
            return conn.execute(select(self.evidence.c.payload).where(self.evidence.c.id == eid)).scalar_one()

    def close(self):
        self.engine.dispose()

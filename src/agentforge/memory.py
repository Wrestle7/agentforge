"""Namespaced memory: lexical hash, local ONNX, or independent embedding API."""
import hashlib
import math
import re
import uuid

import httpx
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, JSON, MetaData, String, Table, inspect, select, text


class Memory:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.postgres = store.engine.dialect.name == "postgresql"
        self.embedding_id = f"{settings.embedding_backend}:{settings.embedding_model}:{settings.embedding_dim}"
        self._model = None
        # Never ALTER the existing vector(256) column or mix embedding spaces.
        legacy = settings.embedding_backend == "hash" and settings.embedding_dim == 256
        identity = self.embedding_id + ":normalized-v1"
        if settings.embedding_backend == "api":
            identity += ":" + (settings.embedding_base_url or settings.llm_base_url)
        table_name = "af_memory" if legacy else "af_memory_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        if self.postgres:
            with store.engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        meta = MetaData()
        self.table = Table(table_name, meta, Column("id", String(40), primary_key=True),
                           Column("namespace", String(100), index=True), Column("source_run", String(80)),
                           Column("embedding_id", String(160)), Column("body", JSON),
                           Column("embedding", Vector(settings.embedding_dim) if self.postgres else JSON))
        meta.create_all(store.engine)

    def embed(self, value, *, query=False):
        cfg = self.settings
        if not isinstance(value, str) or not value.strip():
            raise ValueError("embedding input must be non-empty text")
        if cfg.embedding_backend == "local":
            if self._model is None:
                from fastembed import TextEmbedding
                supported = {m["model"]: m for m in TextEmbedding.list_supported_models()}
                if cfg.embedding_model not in supported:
                    raise ValueError("model is not supported by the installed FastEmbed version")
                if supported[cfg.embedding_model]["dim"] != cfg.embedding_dim:
                    raise ValueError("AF_EMBEDDING_DIM does not match local model dimension")
                self._model = TextEmbedding(model_name=cfg.embedding_model,
                    cache_dir=str(cfg.embedding_cache), threads=2,
                    providers=["CPUExecutionProvider"])
            method = self._model.query_embed if query else self._model.passage_embed
            return self._normalize(next(iter(method([value]))).tolist())
        if cfg.embedding_backend == "api":
            return self._api_embed([value])[0]
        if cfg.embedding_backend != "hash":
            raise ValueError("AF_EMBEDDING_BACKEND must be hash, local or api")
        vector = [0.0] * cfg.embedding_dim
        # Unicode word + individual CJK characters. Stable across processes unlike hash().
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.lower())
        for token in tokens:
            idx = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % len(vector)
            vector[idx] += 1
        norm = math.sqrt(sum(v * v for v in vector)) or 1
        return [v / norm for v in vector]

    def _api_embed(self, values):
        cfg = self.settings
        if not 1 <= len(values) <= 10:
            raise ValueError("embedding batch must contain 1 to 10 texts")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("embedding input must be non-empty text")
        endpoint = (cfg.embedding_base_url or cfg.llm_base_url).rstrip("/")
        if "YOUR_WORKSPACE_ID" in endpoint:
            raise ValueError("Fill AF_EMBEDDING_BASE_URL with your provider's actual Base URL")
        key = cfg.embedding_api_key or (cfg.llm_api_key if not cfg.embedding_base_url else "")
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(endpoint + "/embeddings",
                    headers={"Authorization": f"Bearer {key}"} if key else {},
                    json={"model": cfg.embedding_model, "input": values[0] if len(values) == 1 else values,
                          "encoding_format": "float", "dimensions": cfg.embedding_dim})
                response.raise_for_status()
                rows = response.json()["data"]
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"embedding service returned HTTP {exc.response.status_code}") from None
        except httpx.RequestError as exc:
            raise RuntimeError(f"embedding service connection failed: {type(exc).__name__}") from None
        if len(rows) != len(values):
            raise ValueError("embedding batch size mismatch")
        indexed = {row.get("index", 0): row["embedding"] for row in rows}
        if set(indexed) != set(range(len(values))):
            raise ValueError("embedding response has missing or duplicate indexes")
        return [self._normalize(indexed[i]) for i in range(len(values))]

    def _normalize(self, vector):
        if len(vector) != self.settings.embedding_dim:
            raise ValueError("embedding dimension mismatch")
        vector = [float(v) for v in vector]
        if not all(math.isfinite(v) for v in vector):
            raise ValueError("embedding contains non-finite values")
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            raise ValueError("embedding has zero norm")
        return [v / norm for v in vector]

    def reembed(self, source_table="af_memory", batch_size=10):
        """Copy memory bodies to this model's table; resumable by original row ID.

        Read-only on source. Each successful record is committed independently;
        a network/model failure leaves progress resumable without duplicate rows.
        """
        if not 1 <= batch_size <= 10:
            raise ValueError("migration batch size must be between 1 and 10")
        if source_table == self.table.name:
            raise ValueError("source and destination memory tables must differ")
        if not re.fullmatch(r"af_memory(?:_[a-f0-9]{16})?", source_table):
            raise ValueError("source must be an AgentForge memory table")
        if source_table not in inspect(self.store.engine).get_table_names():
            return {"source": source_table, "target": self.table.name, "copied": 0, "skipped": 0}
        source = Table(source_table, MetaData(), autoload_with=self.store.engine)
        copied = skipped = 0
        after = ""
        while True:
            with self.store.engine.connect() as conn:
                rows = list(conn.execute(select(source).where(source.c.id > after)
                    .order_by(source.c.id).limit(batch_size)).mappings())
            if not rows:
                break
            after = rows[-1]["id"]
            with self.store.engine.connect() as conn:
                existing = set(conn.execute(select(self.table.c.id).where(
                    self.table.c.id.in_([row["id"] for row in rows]))).scalars())
            pending = [row for row in rows if row["id"] not in existing]
            skipped += len(rows) - len(pending)
            if not pending:
                continue
            if self.settings.embedding_backend == "api":
                vectors = self._api_embed([row["body"]["text"] for row in pending])
            else:
                # Generator preserves progress per row if a local backend fails mid-page.
                vectors = (self.embed(row["body"]["text"]) for row in pending)
            for row, vector in zip(pending, vectors):
                with self.store.engine.begin() as conn:
                    # ON CONFLICT supports retries and two concurrent migration invocations.
                    if self.postgres:
                        from sqlalchemy.dialects.postgresql import insert
                    else:
                        from sqlalchemy.dialects.sqlite import insert
                    inserted = conn.execute(insert(self.table).values(id=row["id"], namespace=row["namespace"],
                        source_run=row["source_run"], body=row["body"], embedding_id=self.embedding_id,
                        embedding=vector).on_conflict_do_nothing(index_elements=["id"])
                        .returning(self.table.c.id)).scalar_one_or_none()
                    # psycopg may report rowcount=-1; RETURNING is unambiguous.
                    copied += int(inserted is not None)
                    skipped += int(inserted is None)
        return {"source": source_table, "target": self.table.name, "copied": copied, "skipped": skipped}

    def add(self, namespace, source_run, body):
        vector = self.embed(body["text"])
        with self.store.engine.begin() as conn:
            conn.execute(self.table.insert().values(id=uuid.uuid4().hex, namespace=namespace,
                         source_run=source_run, body=body, embedding_id=self.embedding_id, embedding=vector))

    def search(self, namespace, query, limit=3, exclude_run=""):
        vector = self.embed(query, query=True)
        stmt = select(self.table).where(self.table.c.namespace == namespace,
                                       self.table.c.embedding_id == self.embedding_id,
                                       self.table.c.source_run != exclude_run)
        if self.postgres:
            stmt = stmt.order_by(self.table.c.embedding.cosine_distance(vector)).limit(limit)
        with self.store.engine.connect() as conn:
            rows = list(conn.execute(stmt).mappings())
        if not self.postgres:
            rows.sort(key=lambda r: sum(a * b for a, b in zip(vector, r["embedding"])), reverse=True)
        return [{"source_run": r["source_run"], **r["body"]} for r in rows[:limit]]

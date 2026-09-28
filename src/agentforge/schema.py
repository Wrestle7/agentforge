from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    objective: str = Field(min_length=1, max_length=2000)
    capability: Literal["web", "rag", "sql", "code", "mcp"]
    dependencies: list[str] = Field(default_factory=list)
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # References in arguments: {"$ref": "step_id.rows.0.value"}
    # Resolved only from completed dependency evidence, never from model eval().


class Plan(BaseModel):
    steps: list[Step] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_dag(self):
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step IDs")
        for step in self.steps:
            if set(step.dependencies) - set(ids) or step.id in step.dependencies:
                raise ValueError("unknown/self dependency")
        visited = set()
        while len(visited) < len(ids):
            ready = [s.id for s in self.steps if s.id not in visited and set(s.dependencies) <= visited]
            if not ready:
                raise ValueError("DAG contains a cycle")
            visited.update(ready)
        return self


class Selection(BaseModel):
    tool: str
    arguments: dict[str, Any]


class Critique(BaseModel):
    decision: Literal["continue", "retry", "replan", "terminate"]
    reason: str


class Report(BaseModel):
    answer: str
    evidence_ids: list[str]


class ReactAction(BaseModel):
    done: bool = False
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


def resolve_references(value: Any, results: dict, allowed: set[str]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            parts = value["$ref"].split(".")
            if parts[0] not in allowed:
                raise ValueError("reference must name a completed dependency")
            out = results[parts[0]]
            for key in parts[1:]:
                out = out[int(key)] if isinstance(out, list) else out[key]
            return out
        return {k: resolve_references(v, results, allowed) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_references(v, results, allowed) for v in value]
    return value

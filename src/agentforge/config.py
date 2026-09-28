from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AF_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    database_url: str = ""
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_thinking: Literal["auto", "enabled", "disabled"] = "auto"
    corpus_path: Path | None = None
    tavily_api_key: str = ""
    max_iterations: int = Field(20, ge=1, le=200)
    max_tool_calls: int = Field(40, ge=1, le=1000)
    max_tokens: int = Field(30000, ge=1)
    max_model_output: int = Field(2000, ge=1)
    max_seconds: float = Field(300, gt=0)
    input_price: float | None = Field(None, ge=0)
    output_price: float | None = Field(None, ge=0)
    embedding_backend: Literal["hash", "local", "api"] = "hash"
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_dim: int = Field(256, ge=8)
    embedding_cache: Path = Path("data/models")

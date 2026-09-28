import pytest

from agentforge.config import Settings
from agentforge.fixtures import initialize
from agentforge.runtime import Runtime
from agentforge.tools import build_registry


@pytest.fixture
def runtime(tmp_path):
    cfg = Settings(data_dir=tmp_path, database_url="", _env_file=None)
    corpus, cases, analytics = initialize(tmp_path)
    with Runtime(cfg) as runtime:
        runtime.registry = build_registry(cfg, corpus, analytics)
        runtime.case = cases[0]
        yield runtime

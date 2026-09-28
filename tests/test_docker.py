import os

import pytest

from agentforge.tools import ToolFault, docker_python


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AF_TEST_DOCKER"), reason="set AF_TEST_DOCKER=1 to test Docker execution")
async def test_docker_code_execution():
    result = await docker_python({"code": "import os; print(6*7); print(os.getuid())"})
    assert result["stdout"].splitlines() == ["42", "65534"]


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AF_TEST_DOCKER"), reason="set AF_TEST_DOCKER=1")
async def test_docker_output_is_bounded():
    with pytest.raises(ToolFault, match="16 KiB"):
        await docker_python({"code": "print('x' * 20000)"})


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AF_TEST_DOCKER"), reason="set AF_TEST_DOCKER=1")
async def test_docker_timeout():
    with pytest.raises(TimeoutError):
        await docker_python({"code": "while True: pass"})

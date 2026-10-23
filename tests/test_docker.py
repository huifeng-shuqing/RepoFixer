import pytest

from repofixer.environment import DockerEnv, docker_available

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker daemon not running")


def test_docker_echo_and_network_isolation(ws):
    (ws / "f.txt").write_text("data", encoding="utf-8")
    env = DockerEnv(ws)
    try:
        r = env.run("echo docker-ok && cat f.txt")
        assert r.ok
        assert "docker-ok" in r.stdout and "data" in r.stdout
        # --network none: outbound connections must fail
        r2 = env.run(
            "python -c \"import socket; socket.create_connection(('8.8.8.8', 53), 2)\"",
            timeout=15,
        )
        assert not r2.ok
    finally:
        env.close()

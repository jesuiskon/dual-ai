import subprocess
from pathlib import Path

import pytest

from dualai.agents import AgentRequest

PASS = 'python -c "pass"'
FAIL = 'python -c "import sys; sys.exit(1)"'


def req(prompt, cwd, access="read", schema=None):
    return AgentRequest(prompt=prompt, cwd=Path(cwd), access=access, schema=schema, timeout_s=5)


def git(cwd, *args):
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout


def fake_spawn(returncode, stdout="", stderr="", last_message=None):
    """Substituto de subprocess.run. Guarda o que recebeu em .args e .kwargs."""
    def spawn(args, **kwargs):
        spawn.args, spawn.kwargs = args, kwargs
        if last_message is not None:
            Path(args[args.index("--output-last-message") + 1]).write_text(last_message, encoding="utf-8")
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)
    return spawn


def raises(exc):
    def spawn(args, **kwargs):
        raise exc
    return spawn


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "meu repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Teste")
    git(path, "config", "user.email", "teste@example.invalid")
    (path / "a.txt").write_text("original\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-m", "inicial")
    return path

import json
import threading
import time
from dataclasses import replace

import pytest
from conftest import PASS, fake_spawn, req

from dualai.agents import AgentResult, FakeAgent, run_claude, run_codex
from dualai.pipeline import RunConfig, run
from dualai.proc import Cancelled, CancelToken, run_process
from dualai.runlog import RunLog
from dualai.workspace import create_worktree, run_check

SLEEP_30 = 'python -c "import time; time.sleep(30)"'


def writes(rel, content, text=""):
    def step(request):
        (request.cwd / rel).write_text(content, encoding="utf-8")
        return AgentResult("ok", text=text)
    return step


def cfg(repo, author, critic, token=None, test_cmd=PASS, on_start=None):
    return RunConfig(task="Criar b.py", repo=repo, test_cmd=test_cmd, author=author, critic=critic,
                     author_name="fake", critic_name="fake", cancel=token, on_start=on_start)


def cancel_after(token, seconds):
    timer = threading.Timer(seconds, token.cancel)
    timer.start()
    return timer


def test_cancel_kills_a_running_process_tree():
    token = CancelToken()
    cancel_after(token, 1)
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_process(SLEEP_30, shell=True, cancel=token)
    assert time.monotonic() - started < 15


def test_a_cancelled_token_refuses_to_start_a_process():
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        run_process('python -c "pass"', shell=True, cancel=token)


def test_a_process_without_a_token_runs_as_before():
    assert run_process('python -c "print(1)"', shell=True).stdout.strip() == "1"


def test_check_command_is_killed_on_cancel(repo):
    token = CancelToken()
    cancel_after(token, 1)
    with pytest.raises(Cancelled):
        run_check(create_worktree(repo, "r1"), SLEEP_30, 60, cancel=token)


def test_interrupt_between_stages_ends_the_run_as_cancelled(repo):
    token = CancelToken()

    def author(request):
        (request.cwd / "b.py").write_text("x = 1", encoding="utf-8")
        token.cancel()
        return AgentResult("ok")

    critic = FakeAgent([])
    out = run(cfg(repo, FakeAgent([author]), critic, token))
    assert (out.status, critic.requests) == ("cancelled", [])
    assert RunLog(repo, out.run_id).read_json("state.json")["status"] == "cancelled"
    assert (RunLog(repo, out.run_id).dir / "summary.md").exists()


def test_interrupt_kills_the_test_command(repo):
    token = CancelToken()
    cancel_after(token, 1)
    started = time.monotonic()
    out = run(cfg(repo, FakeAgent([writes("b.py", "x = 1")]), FakeAgent([]), token, test_cmd=SLEEP_30))
    assert out.status == "cancelled" and time.monotonic() - started < 15


def test_agents_receive_the_token_and_hand_it_to_the_process(repo, tmp_path):
    token = CancelToken()
    author = FakeAgent([writes("b.py", "x = 1")])
    run(cfg(repo, author, None, token))
    assert author.requests[0].cancel is token
    request = replace(req("p", tmp_path), cancel=token)
    codex = fake_spawn(0, last_message="ok")
    run_codex(request, spawn=codex, launcher=["codex-de-teste"])
    claude = fake_spawn(0, stdout=json.dumps({"is_error": False, "result": "ok"}))
    run_claude(request, spawn=claude, executable="claude-de-teste")
    assert codex.kwargs["cancel"] is token and claude.kwargs["cancel"] is token


def test_stage_and_author_text_are_recorded(repo):
    seen = {}

    def critic(request):
        seen["stage"] = json.loads(
            (request.cwd.parent.parent / "runs" / request.cwd.name / "state.json").read_text(encoding="utf-8"))["stage"]
        return AgentResult("ok", data={"findings": []})

    out = run(cfg(repo, FakeAgent([writes("b.py", "x = 1", text="Criei **b.py**.")]), FakeAgent([critic])))
    log = RunLog(repo, out.run_id)
    assert seen["stage"] == "critic"
    assert (log.dir / "author.md").read_text(encoding="utf-8") == "Criei **b.py**."
    state = log.read_json("state.json")
    assert (state["stage"], state["tests_1_pass"], state["status"]) == ("closed", True, "done")


def test_on_start_reports_the_run_id_before_the_first_call(repo):
    order = []

    def author(request):
        order.append("author")
        (request.cwd / "b.py").write_text("x = 1", encoding="utf-8")
        return AgentResult("ok")

    out = run(cfg(repo, FakeAgent([author]), None, on_start=lambda run_id: order.append(run_id)))
    assert order == [out.run_id, "author"]

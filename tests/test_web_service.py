import threading
import time

import pytest
from conftest import FAIL, PASS, git

from dualai.agents import fake_author, fake_critic
from dualai import pipeline
from dualai.web import service as service_module
from dualai.runlog import RunLog
from dualai.web.service import Service, ServiceError
from dualai.workspace import WorkspaceError, worktree_path


@pytest.fixture
def service(tmp_path):
    instance = Service(tmp_path / "ui.json", fake_delay_s=0)
    yield instance
    instance.shutdown()


@pytest.fixture
def slow(tmp_path):
    instance = Service(tmp_path / "ui.json", fake_delay_s=5)
    yield instance
    instance.shutdown()


def ready(service, repo, test_cmd=PASS):
    service.select_repo(str(repo))
    service.save_settings(str(repo), test_cmd)
    return str(repo)


def finish(service, repo, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = service.run_detail(repo, run_id)
        if detail["status"] != "running":
            return detail
        time.sleep(0.05)
    raise AssertionError("a execução não terminou a tempo")


def status_of(error):
    return error.value.status


def test_folder_that_is_not_a_repository_is_refused(service, tmp_path):
    with pytest.raises(ServiceError) as error:
        service.select_repo(str(tmp_path))
    assert status_of(error) == 400 and service.state()["current"] is None


def test_only_repositories_selected_in_the_interface_are_served(service, repo):
    for call in (lambda: service.list_runs(str(repo)), lambda: service.start_run(str(repo), "t", fake=True)):
        with pytest.raises(ServiceError) as error:
            call()
        assert status_of(error) == 400


def test_a_task_needs_a_test_command_and_some_text(service, repo):
    service.select_repo(str(repo))
    with pytest.raises(ServiceError) as error:
        service.start_run(str(repo), "t", fake=True)
    assert status_of(error) == 400 and "testes" in str(error.value)
    service.save_settings(str(repo), PASS)
    with pytest.raises(ServiceError) as error:
        service.start_run(str(repo), "   ", fake=True)
    assert status_of(error) == 400
    assert service.list_runs(str(repo)) == []


def test_rehearsal_run_is_reported_from_what_the_run_folder_holds(service, repo):
    path = ready(service, repo)
    run_id = service.start_run(path, "Minha tarefa\nsegunda linha", fake=True)["run_id"]
    detail = finish(service, path, run_id)
    assert (detail["status"], detail["stage"], detail["fake"], detail["calls_used"]) == ("done", "closed", True, 3)
    assert detail["task"] == "Minha tarefa\nsegunda linha" and detail["author_text"] == "criei DUALAI_FAKE.txt"
    assert detail["files"] == ["DUALAI_FAKE.txt"] and "DUALAI_FAKE.txt" in detail["diff"]
    assert (detail["tests_1_pass"], detail["tests_pass"], detail["test_cmd"]) == (True, True, PASS)
    assert [f["severity"] for f in detail["review"]["blocking"]] == ["relevante"]
    assert len(detail["review"]["backlog"]) == 1
    assert detail["resolution"]["resolutions"][0]["state"] == "resolved"
    assert (detail["can_decide"], detail["can_discard"], detail["decision"]) == (True, True, None)
    listed = service.list_runs(path)[0]
    assert (listed["id"], listed["title"], listed["status"], listed["fake"]) == (run_id, "Minha tarefa", "done", True)
    assert service.state()["active"] is None


def test_failing_tests_ask_for_a_decision(service, repo):
    path = ready(service, repo, FAIL)
    detail = finish(service, path, service.start_run(path, "t", fake=True)["run_id"])
    assert (detail["status"], detail["tests_pass"], detail["can_decide"]) == ("needs_decision", False, True)


def test_approve_takes_the_result_to_the_branch(service, repo):
    path = ready(service, repo)
    run_id = service.start_run(path, "t", fake=True)["run_id"]
    finish(service, path, run_id)
    detail = service.approve(path, run_id)
    assert (repo / "DUALAI_FAKE.txt").exists() and git(repo, "status", "--porcelain") == ""
    assert (detail["decision"], detail["can_decide"], detail["can_discard"]) == ("approved", False, False)
    with pytest.raises(ServiceError):
        service.approve(path, run_id)


def test_discard_leaves_the_branch_alone(service, repo):
    path = ready(service, repo)
    run_id = service.start_run(path, "t", fake=True)["run_id"]
    finish(service, path, run_id)
    detail = service.discard(path, run_id)
    assert not (repo / "DUALAI_FAKE.txt").exists() and detail["decision"] == "discarded"


def test_a_second_task_is_refused_while_one_is_running(slow, repo):
    path = ready(slow, repo)
    run_id = slow.start_run(path, "primeira", fake=True)["run_id"]
    assert slow.state()["active"] == {"repo": path, "run_id": run_id}
    with pytest.raises(ServiceError) as error:
        slow.start_run(path, "segunda", fake=True)
    assert status_of(error) == 409 and len(slow.list_runs(path)) == 1


def test_interrupt_ends_the_run_as_cancelled_and_only_discard_remains(slow, repo):
    path = ready(slow, repo)
    run_id = slow.start_run(path, "t", fake=True)["run_id"]
    assert slow.run_detail(path, run_id)["status"] == "running"
    with pytest.raises(ServiceError) as error:
        slow.discard(path, run_id)
    assert status_of(error) == 409
    assert slow.interrupt() == {"interrupting": True}
    detail = finish(slow, path, run_id, timeout=15)
    assert (detail["status"], detail["can_decide"], detail["can_discard"]) == ("cancelled", False, True)
    with pytest.raises(ServiceError):
        slow.approve(path, run_id)
    assert slow.discard(path, run_id)["decision"] == "discarded"
    assert slow.interrupt() == {"interrupting": False}


def test_shutdown_interrupts_the_active_run(slow, repo):
    path = ready(slow, repo)
    run_id = slow.start_run(path, "t", fake=True)["run_id"]
    slow.shutdown()
    assert RunLog(repo, run_id).read_json("state.json")["status"] == "cancelled"


def test_a_run_left_running_by_a_dead_process_is_shown_as_interrupted(service, repo):
    path = ready(service, repo)
    log = RunLog(repo, "20261115-000000-antiga")
    log.write_text("task.md", "Tarefa antiga")
    log.set_state(status="running", author="claude", critic="codex", calls_used=1, stage="author")
    worktree_path(repo, log.run_id).mkdir(parents=True)
    detail = service.run_detail(path, "20261115-000000-antiga")
    assert (detail["status"], detail["can_decide"]) == ("interrupted", False)
    assert detail["recovery_required"] is True and detail["can_discard"] is False
    assert service.list_runs(path)[0]["status"] == "interrupted"


@pytest.mark.parametrize("bad", ["../x", "a/b", "nao-existe"])
def test_unknown_or_path_like_run_ids_are_refused(service, repo, bad):
    path = ready(service, repo)
    with pytest.raises(ServiceError) as error:
        service.run_detail(path, bad)
    assert status_of(error) == 404


def test_history_and_settings_survive_a_restart(service, repo, tmp_path):
    path = ready(service, repo)
    run_id = service.start_run(path, "t", fake=True)["run_id"]
    finish(service, path, run_id)
    again = Service(tmp_path / "ui.json", fake_delay_s=0)
    state = again.state()
    assert (state["current"]["path"], state["current"]["test_cmd"], state["current"]["name"]) == (path, PASS, "meu repo")
    assert state["recent"][0]["path"] == path and again.list_runs(path)[0]["id"] == run_id


def test_folder_dialog_result_is_validated_like_a_typed_path(tmp_path, repo):
    picked = Service(tmp_path / "a.json", picker=lambda: str(repo)).pick_repo()
    assert picked["picked"] is True and picked["current"]["name"] == "meu repo"
    assert Service(tmp_path / "b.json", picker=lambda: "").pick_repo()["picked"] is False
    with pytest.raises(ServiceError):
        Service(tmp_path / "c.json", picker=lambda: str(tmp_path)).pick_repo()


def test_real_mode_uses_the_configured_agents_with_fixed_roles(tmp_path, repo):
    service = Service(tmp_path / "ui.json",
                      agents={"author": ("claude", fake_author), "critic": ("codex", fake_critic)})
    path = ready(service, repo)
    detail = finish(service, path, service.start_run(path, "t")["run_id"])
    assert (detail["author"], detail["critic"], detail["fake"], detail["status"]) == ("claude", "codex", False, "done")


def test_start_timeout_cancels_before_any_agent_and_keeps_cleanup_visible(monkeypatch, tmp_path, repo):
    entered, release = threading.Event(), threading.Event()
    original_create = pipeline.create_worktree
    called = []

    def delayed_create(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_create(*args, **kwargs)

    def unexpected_agent(request):
        called.append(request)
        raise AssertionError("o início expirado não deve chamar agente")

    monkeypatch.setattr(pipeline, "create_worktree", delayed_create)
    monkeypatch.setattr(service_module, "START_TIMEOUT_S", 0.02)
    instance = Service(tmp_path / "timeout-ui.json", agents={
        "author": ("synthetic", unexpected_agent), "critic": ("synthetic", unexpected_agent)})
    path = ready(instance, repo)
    try:
        with pytest.raises(ServiceError) as error:
            instance.start_run(path, "início lento")
        assert status_of(error) == 504 and "interrupção" in str(error.value)
        assert entered.wait(1)
        assert instance.state()["active"] == {
            "repo": path, "run_id": None, "status": "preparing", "interrupting": True}
        with pytest.raises(ServiceError) as duplicate:
            instance.start_run(path, "não iniciar outra", fake=True)
        assert status_of(duplicate) == 409
        worker = instance._active["thread"]
        release.set()
        worker.join(5)
        assert not worker.is_alive() and instance.state()["active"] is None
        runs = instance.list_runs(path)
        assert len(runs) == 1 and runs[0]["status"] == "cancelled"
        assert called == []
    finally:
        release.set()
        instance.shutdown()


def test_preparing_run_is_visible_and_can_be_interrupted(monkeypatch, tmp_path, repo):
    entered, release = threading.Event(), threading.Event()
    original_create = pipeline.create_worktree
    result = {}

    def delayed_create(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(pipeline, "create_worktree", delayed_create)
    instance = Service(tmp_path / "preparing-ui.json", fake_delay_s=0)
    path = ready(instance, repo)

    def start():
        result.update(instance.start_run(path, "interromper preparação", fake=True))

    caller = threading.Thread(target=start)
    caller.start()
    try:
        assert entered.wait(2)
        assert instance.state()["active"] == {
            "repo": path, "run_id": None, "status": "preparing", "interrupting": False}
        assert instance.interrupt() == {"interrupting": True}
        assert instance.state()["active"]["interrupting"] is True
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        detail = finish(instance, path, result["run_id"])
        assert detail["status"] == "cancelled" and detail["calls_used"] == 0
    finally:
        release.set()
        caller.join(5)
        instance.shutdown()


def test_preparation_failure_returns_error_without_leaving_active_work(monkeypatch, service, repo):
    path = ready(service, repo)

    def failed_checkout(config):
        raise WorkspaceError("falha sintética no checkout")

    monkeypatch.setattr(service_module, "run", failed_checkout)
    with pytest.raises(ServiceError) as error:
        service.start_run(path, "início recusado", fake=True)
    assert status_of(error) == 400 and "falha sintética" in str(error.value)
    assert service.state()["active"] is None

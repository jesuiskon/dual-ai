import pytest
from conftest import PASS, git

from dualai.actions import ActionError, approve_run, discard_run, require_run
from dualai.agents import AgentResult, FakeAgent, fake_author, fake_critic
from dualai.pipeline import RunConfig, run
from dualai.runlog import RunLog
from dualai.workspace import WorkspaceError, worktree_path


def fake_run(repo, test_cmd=PASS):
    return run(RunConfig(task="Minha tarefa", repo=repo, test_cmd=test_cmd, author=fake_author,
                         critic=fake_critic, author_name="fake", critic_name="fake"))


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "..", "", "nao-existe"])
def test_invalid_or_unknown_run_ids_are_rejected(repo, bad):
    with pytest.raises(ActionError):
        require_run(repo, bad)


def test_approve_works_without_the_cli(repo):
    out = fake_run(repo)
    approve_run(repo, out.run_id)
    assert (repo / "DUALAI_FAKE.txt").exists()
    assert "Minha tarefa" in (repo / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert RunLog(repo, out.run_id).read_json("state.json")["decision"] == "approved"
    assert git(repo, "status", "--porcelain") == ""


def test_discard_works_without_the_cli(repo):
    out = fake_run(repo)
    discard_run(repo, out.run_id)
    assert not (repo / "DUALAI_FAKE.txt").exists()
    assert RunLog(repo, out.run_id).read_json("state.json")["decision"] == "discarded"


def test_a_run_cannot_be_decided_twice(repo):
    out = fake_run(repo)
    approve_run(repo, out.run_id)
    with pytest.raises(ActionError):
        approve_run(repo, out.run_id)
    with pytest.raises(ActionError):
        discard_run(repo, out.run_id)


def test_rehearsal_author_changes_something_on_every_run(repo):
    approve_run(repo, fake_run(repo).run_id)
    assert fake_run(repo).status == "done"


@pytest.mark.parametrize("mutation", ["working-file", "index", "new-file", "head"])
def test_approve_refuses_results_modified_after_review(repo, mutation):
    out = fake_run(repo)
    wt = worktree_path(repo, out.run_id)
    before = git(repo, "rev-parse", "HEAD")
    if mutation == "new-file":
        (wt / "new.py").write_text("unreviewed = True")
    elif mutation == "head":
        git(wt, "commit", "-m", "commit depois da revisão")
    else:
        (wt / "DUALAI_FAKE.txt").write_text("resultado não revisado")
        if mutation == "index":
            git(wt, "add", "-A")
    with pytest.raises(WorkspaceError, match="após a revisão"):
        approve_run(repo, out.run_id)
    assert git(repo, "rev-parse", "HEAD") == before
    assert not (repo / "DUALAI_FAKE.txt").exists()
    assert wt.exists() and not (wt / "docs" / "decisions.md").exists()


def test_approve_retry_preserves_original_local_files_and_records_one_decision(repo):
    out = fake_run(repo)
    wt = worktree_path(repo, out.run_id)
    conflict = repo / "DUALAI_FAKE.txt"
    conflict.write_text("rascunho original do usuário")
    with pytest.raises(WorkspaceError):
        approve_run(repo, out.run_id)
    assert conflict.read_text() == "rascunho original do usuário"
    log = RunLog(repo, out.run_id)
    assert log.read_json("state.json")["approval_snapshot"]
    assert (wt / "docs" / "decisions.md").read_text().count(out.run_id) == 1
    conflict.unlink()  # somente o arquivo criado por este teste no repositório temporário
    approve_run(repo, out.run_id)
    assert (repo / "docs" / "decisions.md").read_text().count(out.run_id) == 1
    assert log.read_json("state.json")["decision"] == "approved"


def test_discard_refuses_a_running_execution_even_outside_the_web_service(repo):
    out = fake_run(repo)
    RunLog(repo, out.run_id).set_state(status="running")
    wt = worktree_path(repo, out.run_id)
    with pytest.raises(ActionError, match="em andamento"):
        discard_run(repo, out.run_id)
    assert wt.exists()
    assert (wt / "DUALAI_FAKE.txt").exists()
    assert not (repo / "DUALAI_FAKE.txt").exists()


def test_approval_does_not_follow_a_decision_symlink_outside_the_worktree(repo):
    original = repo / "private-notes.txt"
    original.write_text("anotações originais")

    def write_link(request):
        docs = request.cwd / "docs"
        docs.mkdir()
        try:
            (docs / "decisions.md").symlink_to(original)
        except OSError:
            pytest.skip("o sistema não permite criar symlink neste teste")
        return AgentResult("ok")

    out = run(RunConfig(task="Revisar documentação", repo=repo, test_cmd=PASS,
                        author=FakeAgent([write_link]), critic=None,
                        author_name="fake", critic_name="none"))
    assert out.status == "done"
    with pytest.raises(ActionError, match="arquivo regular"):
        approve_run(repo, out.run_id)
    assert original.read_text() == "anotações originais"
    assert worktree_path(repo, out.run_id).exists()

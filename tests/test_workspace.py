import pytest
import subprocess
from conftest import FAIL, git

from dualai import workspace
from dualai.proc import Cancelled

from dualai.workspace import (
    WorkspaceError,
    approve,
    changed_files,
    collect_diff,
    create_worktree,
    discard,
    has_uncommitted_changes,
    reset_to_staged,
    run_check,
    stage_all,
    snapshot,
)


def test_worktree_is_created_and_user_tree_stays_clean(repo):
    wt = create_worktree(repo, "r1")
    assert wt == repo / ".dualai" / "worktrees" / "r1" and (wt / "a.txt").exists()
    assert git(repo, "branch", "--list", "dualai/r1").strip() != ""
    assert ".dualai/" in (repo / ".git" / "info" / "exclude").read_text()
    assert git(repo, "status", "--porcelain") == ""


def test_rejects_folder_that_is_not_a_git_repo(tmp_path):
    with pytest.raises(WorkspaceError):
        create_worktree(tmp_path, "r1")


def test_rejects_repo_without_commits(tmp_path):
    git(tmp_path, "init", "-b", "main")
    with pytest.raises(WorkspaceError):
        create_worktree(tmp_path, "r1")


def test_diff_includes_modified_and_new_files(repo):
    wt = create_worktree(repo, "r1")
    (wt / "a.txt").write_text("mudou")
    (wt / "sub").mkdir()
    (wt / "sub" / "b.txt").write_text("novo")
    assert changed_files(wt) == ["a.txt", "sub/b.txt"]
    assert "mudou" in collect_diff(wt) and "novo" in collect_diff(wt)


def test_has_uncommitted_changes(repo):
    assert has_uncommitted_changes(repo) is False
    (repo / "a.txt").write_text("x")
    assert has_uncommitted_changes(repo) is True


def test_check_passes_fails_and_times_out(repo):
    wt = create_worktree(repo, "r1")
    ok = run_check(wt, 'python -c "print(\'tudo certo\')"', 30)
    assert ok.passed and "tudo certo" in ok.output
    assert run_check(wt, FAIL, 30).passed is False
    slow = run_check(wt, 'python -c "import time; time.sleep(30)"', 1)
    assert slow.passed is False and "timeout" in slow.output
    assert slow.seconds < 15  # a árvore de processos foi encerrada, sem esperar os 30 s


def test_approve_merges_and_cleans_up(repo):
    wt = create_worktree(repo, "r1")
    (wt / "b.txt").write_text("novo")
    stage_all(wt)
    (wt / "sobra-dos-testes.tmp").write_text("x")
    approve(repo, "r1", "dualai: tarefa")
    assert not (repo / "sobra-dos-testes.tmp").exists()
    assert (repo / "b.txt").read_text() == "novo" and not wt.exists()
    assert git(repo, "branch", "--list", "dualai/r1").strip() == ""


def test_approve_refuses_when_user_branch_moved(repo):
    wt = create_worktree(repo, "r1")
    (wt / "b.txt").write_text("novo")
    stage_all(wt)
    (repo / "c.txt").write_text("outro")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "outro")
    with pytest.raises(WorkspaceError):
        approve(repo, "r1", "dualai: tarefa")
    assert not (repo / "b.txt").exists() and wt.exists()


def test_discard_removes_worktree_and_branch(repo):
    wt = create_worktree(repo, "r1")
    (wt / "b.txt").write_text("novo")
    discard(repo, "r1")
    assert not wt.exists() and git(repo, "branch", "--list", "dualai/r1").strip() == ""
    assert git(repo, "status", "--porcelain") == ""


def test_approve_and_discard_reject_unknown_run(repo):
    with pytest.raises(WorkspaceError):
        approve(repo, "nao-existe", "m")
    with pytest.raises(WorkspaceError):
        discard(repo, "nao-existe")


def test_reset_to_staged_drops_what_the_test_command_left(repo):
    wt = create_worktree(repo, "r1")
    (wt / "b.txt").write_text("novo")
    stage_all(wt)
    (wt / "lixo.tmp").write_text("x")
    (wt / "b.txt").write_text("mexido pelos testes")
    reset_to_staged(wt)
    assert not (wt / "lixo.tmp").exists() and (wt / "b.txt").read_text() == "novo"


@pytest.mark.parametrize("interrupted", [False, True])
def test_check_restores_immutable_snapshot_when_tests_stage_changes(repo, monkeypatch, interrupted):
    wt = create_worktree(repo, "r1")
    (wt / "b.txt").write_text("resultado revisável")
    stage_all(wt)
    before = snapshot(wt)

    def mutate(*args, **kwargs):
        (wt / "b.txt").write_text("alterado pelo teste")
        (wt / "lixo.tmp").write_text("arquivo adicionado pelo teste")
        git(wt, "add", "-A")
        if interrupted:
            raise Cancelled()
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(workspace, "run_process", mutate)
    if interrupted:
        with pytest.raises(Cancelled):
            run_check(wt, "comando-de-teste", 5)
    else:
        assert run_check(wt, "comando-de-teste", 5).passed
    assert snapshot(wt) == before
    assert (wt / "b.txt").read_text() == "resultado revisável"
    assert not (wt / "lixo.tmp").exists()
    assert (repo / "a.txt").read_text() == "original\n"


def test_check_preserves_evidence_when_tests_change_head(repo, monkeypatch):
    wt = create_worktree(repo, "r1")
    (wt / "b.txt").write_text("resultado")
    stage_all(wt)
    original_head = git(repo, "rev-parse", "HEAD")

    def commit_during_test(*args, **kwargs):
        git(wt, "commit", "-m", "commit inesperado do teste")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(workspace, "run_process", commit_during_test)
    with pytest.raises(WorkspaceError, match="alterou HEAD"):
        run_check(wt, "comando-de-teste", 5)
    assert git(repo, "rev-parse", "HEAD") == original_head
    assert git(wt, "rev-parse", "HEAD") != original_head
    assert wt.exists()

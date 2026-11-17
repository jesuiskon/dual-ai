from conftest import FAIL, PASS, git

from dualai import cli
from dualai.agents import AgentResult, fake_author
from dualai.cli import main
from dualai.runlog import RunLog, runs_dir


def only_run_id(repo):
    (folder,) = list(runs_dir(repo).iterdir())
    return folder.name


def test_fake_run_ends_done_and_prints_the_run_id(repo, capsys):
    code = main(["run", "Adicionar \"saudação\"\ncom acentuação", "--repo", str(repo), "--test-cmd", PASS, "--fake"])
    out = capsys.readouterr().out
    assert code == 0 and "done" in out and "summary.md" in out
    assert only_run_id(repo) in out
    assert "saudação" in RunLog(repo, only_run_id(repo)).dir.joinpath("task.md").read_text(encoding="utf-8")


def test_fake_run_leaves_every_file_of_the_run(repo):
    main(["run", "t", "--repo", str(repo), "--test-cmd", PASS, "--fake"])
    names = {path.name for path in RunLog(repo, only_run_id(repo)).dir.iterdir()}
    assert names == {"task.md", "state.json", "diff-initial.patch", "diff.patch", "tests-1.txt", "tests-2.txt", "review.json",
                     "resolution.json", "backlog.md", "summary.md", "metrics.json", "author.md"}


def test_status_lists_runs(repo, capsys):
    main(["run", "t", "--repo", str(repo), "--test-cmd", PASS, "--fake"])
    capsys.readouterr()
    assert main(["status", "--repo", str(repo)]) == 0
    assert "done" in capsys.readouterr().out


def test_approve_merges_and_records_the_decision(repo):
    main(["run", "Minha tarefa", "--repo", str(repo), "--test-cmd", PASS, "--fake"])
    assert main(["approve", only_run_id(repo), "--repo", str(repo)]) == 0
    assert (repo / "DUALAI_FAKE.txt").exists()
    assert "Minha tarefa" in (repo / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert git(repo, "status", "--porcelain") == ""


def test_discard_leaves_the_repo_untouched(repo):
    main(["run", "t", "--repo", str(repo), "--test-cmd", PASS, "--fake"])
    assert main(["discard", only_run_id(repo), "--repo", str(repo)]) == 0
    assert not (repo / "DUALAI_FAKE.txt").exists()


def test_needs_decision_exits_with_2(repo):
    assert main(["run", "t", "--repo", str(repo), "--test-cmd", FAIL, "--fake"]) == 2


def test_errors_exit_with_1_and_a_plain_message(tmp_path, repo, capsys):
    assert main(["run", "t", "--repo", str(tmp_path / "vazio"), "--test-cmd", PASS, "--fake"]) == 1
    assert main(["approve", "nao-existe", "--repo", str(repo)]) == 1
    assert main(["run", "t", "--repo", str(repo), "--test-cmd", PASS, "--author", "inexistente"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "agente não disponível" in err



def test_approve_can_be_retried_after_a_refusal_without_duplicating_the_decision(repo):
    main(["run", "Minha tarefa", "--repo", str(repo), "--test-cmd", PASS, "--fake"])
    (repo / "c.txt").write_text("outro")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "outro")
    assert main(["approve", only_run_id(repo), "--repo", str(repo)]) == 1
    git(repo, "reset", "--hard", "HEAD~1")
    assert main(["approve", only_run_id(repo), "--repo", str(repo)]) == 0
    assert (repo / "docs" / "decisions.md").read_text(encoding="utf-8").count(only_run_id(repo)) == 1


def test_real_agents_are_registered():
    assert {"codex", "claude"} <= set(cli.AGENTS)


def test_auth_mode_reaches_the_adapter(repo, monkeypatch):
    seen = {}

    def adapter(request, *, auth="subscription"):
        seen["auth"] = auth
        return AgentResult("error", detail="parar aqui")

    monkeypatch.setitem(cli.AGENTS, "codex", adapter)
    base = ["run", "t", "--repo", str(repo), "--test-cmd", PASS, "--author", "codex", "--critic", "none"]
    assert main(base) == 1 and seen["auth"] == "subscription"
    assert main([*base, "--codex-auth", "api_key"]) == 1 and seen["auth"] == "api_key"


def test_relative_repo_path_gives_the_agents_an_absolute_folder(repo, monkeypatch):
    seen = {}

    def adapter(request, *, auth="subscription"):
        seen["cwd"] = request.cwd
        return AgentResult("error", detail="parar aqui")

    monkeypatch.setitem(cli.AGENTS, "codex", adapter)
    monkeypatch.chdir(repo)
    assert main(["run", "t", "--test-cmd", PASS, "--author", "codex", "--critic", "none"]) == 1
    assert seen["cwd"].is_absolute() and seen["cwd"].is_dir()


def test_files_created_by_the_test_command_are_not_committed(repo):
    leaves_a_file = "python -c \"open('lixo.tmp', 'w').write('x')\""
    assert main(["run", "t", "--repo", str(repo), "--test-cmd", leaves_a_file, "--fake"]) == 0
    assert main(["approve", only_run_id(repo), "--repo", str(repo)]) == 0
    assert (repo / "DUALAI_FAKE.txt").exists() and not (repo / "lixo.tmp").exists()
    assert "lixo.tmp" not in git(repo, "show", "--stat", "HEAD")
    assert git(repo, "status", "--porcelain") == ""


def test_only_finished_runs_can_be_approved(repo, monkeypatch, capsys):
    monkeypatch.setitem(cli.AGENTS, "codex", lambda request, *, auth="subscription": AgentResult("error", detail="x"))
    main(["run", "t", "--repo", str(repo), "--test-cmd", PASS, "--author", "codex", "--critic", "none"])
    assert main(["approve", only_run_id(repo), "--repo", str(repo)]) == 1
    assert "failed" in capsys.readouterr().err
    assert main(["discard", only_run_id(repo), "--repo", str(repo)]) == 0


def test_model_flags_reach_author_and_critic_without_calling_providers(repo, monkeypatch):
    seen = {}

    def author(request, *, auth="subscription", model=None):
        seen["claude"] = (auth, model)
        return fake_author(request)

    def critic(request, *, auth="subscription", model=None):
        seen["codex"] = (auth, model)
        return AgentResult("ok", data={"findings": []})

    monkeypatch.setitem(cli.AGENTS, "claude", author)
    monkeypatch.setitem(cli.AGENTS, "codex", critic)
    assert main(["run", "t", "--repo", str(repo), "--test-cmd", PASS,
                 "--claude-model", "claude-de-teste", "--codex-model", "codex-de-teste"]) == 0
    assert seen == {"claude": ("subscription", "claude-de-teste"),
                    "codex": ("subscription", "codex-de-teste")}


def test_help_explains_saved_login_billing_and_optional_models(capsys):
    assert main(["run", "--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "login salvo da CLI" in out and "cobrança depende da conta" in out
    assert "--claude-model" in out and "--codex-model" in out

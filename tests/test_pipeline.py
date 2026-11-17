from dataclasses import replace
from datetime import datetime

import pytest
from conftest import FAIL, PASS, git

from dualai.agents import AgentResult, FakeAgent
from dualai.findings import REVIEW_SCHEMA
from dualai.pipeline import RunConfig, run
from dualai.runlog import RunLog
from dualai.workspace import snapshot

REL = {"severity": "relevante", "title": "Offset errado", "file": "b.py", "line": 1,
       "scenario": "entrada 1; obtido 2; esperado 1", "suggestion": "corrigir"}
CRIT = {**REL, "severity": "critico", "title": "Perde dados"}
PREF = {"severity": "preferencia", "title": "Nome", "file": "", "line": None, "scenario": "", "suggestion": ""}


def writes(rel, content, text=""):
    def step(request):
        path = request.cwd / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return AgentResult("ok", text=text)
    return step


def review(*findings):
    return AgentResult("ok", data={"findings": list(findings)})


def resolution(*triples):
    return AgentResult("ok", data={"resolutions": [
        {"finding_id": finding_id, "state": state, "note": note} for finding_id, state, note in triples]})


def cfg(repo, author, critic, test_cmd=PASS):
    return RunConfig(task="Criar b.py", repo=repo, test_cmd=test_cmd, author=author, critic=critic,
                     author_name="fake", critic_name="fake")


def log_of(repo, out):
    return RunLog(repo, out.run_id)


def test_no_blocking_findings_ends_after_two_calls(repo):
    author, critic = FakeAgent([writes("b.py", "x = 1")]), FakeAgent([review(PREF)])
    out = run(cfg(repo, author, critic))
    assert (out.status, out.calls_used) == ("done", 2)
    assert "Nome" in log_of(repo, out).dir.joinpath("backlog.md").read_text(encoding="utf-8")


def test_blocking_finding_resolved_ends_done_with_three_calls(repo):
    author = FakeAgent([writes("b.py", "x = 1"), resolution(("F1", "resolved", "corrigido"))])
    out = run(cfg(repo, author, FakeAgent([review(REL)])))
    assert (out.status, out.calls_used) == ("done", 3)
    assert log_of(repo, out).read_json("resolution.json")["resolutions"][0]["state"] == "resolved"


def test_critic_is_called_once_even_when_tests_fail_after_resolution(repo):
    author = FakeAgent([writes("b.py", "x = 1"), resolution(("F0", "resolved", "ok"), ("F1", "resolved", "ok"))])
    critic = FakeAgent([review(REL)])
    out = run(cfg(repo, author, critic, test_cmd=FAIL))
    assert (out.status, out.calls_used, len(critic.requests)) == ("needs_decision", 3, 1)


def test_critic_gets_a_read_only_packet_without_the_author_notes(repo):
    author, critic = FakeAgent([writes("b.py", "x = 1", text="NOTAS-DO-AUTOR")]), FakeAgent([review()])
    run(cfg(repo, author, critic))
    request = critic.requests[0]
    assert request.access == "read" and request.schema == REVIEW_SCHEMA
    assert "x = 1" in request.prompt and "NOTAS-DO-AUTOR" not in request.prompt


def test_author_writes_only_inside_the_worktree(repo):
    author = FakeAgent([writes("b.py", "x = 1")])
    out = run(cfg(repo, author, FakeAgent([review()])))
    assert author.requests[0].access == "write"
    assert author.requests[0].cwd == repo / ".dualai" / "worktrees" / out.run_id
    assert git(repo, "status", "--porcelain") == "" and not (repo / "b.py").exists()


def test_justified_relevant_closes_and_shows_the_note(repo):
    author = FakeAgent([writes("b.py", "x = 1"), resolution(("F1", "justified", "fora do contrato"))])
    out = run(cfg(repo, author, FakeAgent([review(REL)])))
    assert out.status == "done"
    assert "fora do contrato" in log_of(repo, out).dir.joinpath("summary.md").read_text(encoding="utf-8")


def test_justified_critical_and_escalated_go_to_the_user(repo):
    for finding, state in ((CRIT, "justified"), (REL, "escalated")):
        author = FakeAgent([writes("b.py", "x = 1"), resolution(("F1", state, "motivo"))])
        assert run(cfg(repo, author, FakeAgent([review(finding)]))).status == "needs_decision"


def test_failing_tests_become_finding_f0_for_the_author(repo):
    author = FakeAgent([writes("b.py", "x = 1"), resolution(("F0", "resolved", "ok"))])
    run(cfg(repo, author, FakeAgent([review()]), test_cmd=FAIL))
    assert "F0" in author.requests[1].prompt


def test_one_repair_call_for_invalid_output_then_failure(repo):
    bad = AgentResult("ok", data={"findings": "x"})
    author = FakeAgent([writes("b.py", "x = 1"), resolution(("F1", "resolved", "ok"))])
    assert run(cfg(repo, author, FakeAgent([bad, review(REL)]))).calls_used == 4
    out = run(cfg(repo, FakeAgent([writes("c.py", "y = 2")]), FakeAgent([bad, bad])))
    assert (out.status, out.reason, out.calls_used) == ("failed", "invalid_output", 3)


def test_the_repair_is_spent_once_per_run_so_calls_never_pass_four(repo):
    bad_review = AgentResult("ok", data={"findings": "x"})
    bad_resolution = AgentResult("ok", data=None, text="corrigi tudo")
    author = FakeAgent([writes("b.py", "x = 1"), bad_resolution])
    out = run(cfg(repo, author, FakeAgent([bad_review, review(REL)])))
    assert (out.status, out.reason, out.calls_used) == ("failed", "invalid_output", 4)


def test_the_repair_call_tells_the_agent_what_was_wrong(repo):
    critic = FakeAgent([AgentResult("ok", data={"findings": [{"severity": "alto"}]}), review()])
    run(cfg(repo, FakeAgent([writes("b.py", "x = 1")]), critic))
    assert "severidade inválida" in critic.requests[1].prompt


def test_usage_limit_stops_the_run(repo):
    critic = FakeAgent([AgentResult("limit_reached", detail="usage limit")])
    out = run(cfg(repo, FakeAgent([writes("b.py", "x = 1")]), critic))
    assert (out.status, out.calls_used) == ("limit_reached", 2)


@pytest.mark.parametrize("status", ["timeout", "error"])
def test_agent_timeout_or_error_fails_the_run(repo, status):
    out = run(cfg(repo, FakeAgent([AgentResult(status, detail="boom")]), FakeAgent([])))
    assert (out.status, out.calls_used) == ("failed", 1) and "boom" in out.reason
    assert log_of(repo, out).read_json("state.json")["status"] == "failed"


def test_no_changes_fails_before_the_critic(repo):
    critic = FakeAgent([])
    out = run(cfg(repo, FakeAgent([AgentResult("ok", text="nada a fazer")]), critic))
    assert (out.status, out.reason, critic.requests) == ("failed", "no_changes", [])


def test_oversized_packet_asks_for_a_decision_before_the_critic(repo):
    critic = FakeAgent([])
    out = run(cfg(repo, FakeAgent([writes("big.txt", "x" * 250_000)]), critic))
    assert (out.status, out.reason, critic.requests) == ("needs_decision", "packet_too_large", [])


def test_interrupt_is_recorded_as_cancelled(repo):
    def interrupt(_request):
        raise KeyboardInterrupt
    out = run(cfg(repo, FakeAgent([interrupt]), FakeAgent([])))
    assert out.status == "cancelled"
    assert log_of(repo, out).read_json("state.json")["status"] == "cancelled"


def test_uncommitted_user_changes_are_left_alone_and_reported(repo):
    (repo / "a.txt").write_text("rascunho do usuário")
    out = run(cfg(repo, FakeAgent([writes("b.py", "x = 1")]), FakeAgent([review()])))
    assert (repo / "a.txt").read_text() == "rascunho do usuário"
    assert "não commitadas" in log_of(repo, out).dir.joinpath("summary.md").read_text(encoding="utf-8")


def test_metrics_match_the_run(repo):
    author = FakeAgent([writes("b.py", "x = 1"), resolution(("F1", "escalated", "não sei"))])
    out = run(cfg(repo, author, FakeAgent([review(REL)])))
    metrics = log_of(repo, out).read_json("metrics.json")
    assert len(metrics["calls"]) == out.calls_used == 3 and metrics["decisions_requested"] == 1
    assert (metrics["author"], metrics["critic"], metrics["status"]) == ("fake", "fake", "needs_decision")
    assert metrics["findings"]["relevante"] == 1 and metrics["resolutions"]["escalated"] == 1


def test_solo_mode_makes_one_call(repo):
    out = run(cfg(repo, FakeAgent([writes("b.py", "x = 1")]), None))
    assert (out.status, out.calls_used) == ("done", 1)


def test_two_runs_in_the_same_second_get_distinct_ids(repo):
    now = datetime(2026, 11, 14, 15, 30, 12)
    first = run(cfg(repo, FakeAgent([writes("b.py", "x = 1")]), None), now=now)
    second = run(cfg(repo, FakeAgent([writes("c.py", "y = 2")]), None), now=now)
    assert second.run_id == first.run_id + "-2"


def test_unknown_mode_is_rejected_before_any_call(repo):
    author = FakeAgent([])
    with pytest.raises(ValueError):
        run(replace(cfg(repo, author, None), mode="debate"))
    assert author.requests == []


def test_final_diff_and_snapshot_include_the_resolution(repo):
    def resolve(request):
        (request.cwd / "b.py").write_text("final = 2\n")
        (request.cwd / "c.py").write_text("added_during_resolution = True\n")
        return resolution(("F1", "resolved", "corrigido"))

    out = run(cfg(repo, FakeAgent([writes("b.py", "initial = 1\n"), resolve]), FakeAgent([review(REL)])))
    log = log_of(repo, out)
    wt = repo / ".dualai" / "worktrees" / out.run_id
    initial = (log.dir / "diff-initial.patch").read_text()
    final = (log.dir / "diff.patch").read_text()
    assert out.status == "done"
    assert "+initial = 1" in initial and "+initial = 1" not in final
    assert "+final = 2" in final and "added_during_resolution" in final
    assert final == git(wt, "diff", "--cached", "HEAD")
    assert log.read_json("state.json")["reviewed_snapshot"] == vars(snapshot(wt))


def test_unexpected_agent_exception_closes_run_without_its_sensitive_message(repo):
    def broken(_request):
        raise ValueError("sensitive-project-content-not-for-logs")

    out = run(cfg(repo, FakeAgent([broken]), None))
    log = log_of(repo, out)
    state = log.read_json("state.json")
    assert (out.status, out.reason) == ("failed", "internal_error:ValueError")
    assert state["status"] == "failed" and state["stage"] == "closed"
    assert "sensitive-project-content-not-for-logs" not in (log.dir / "summary.md").read_text()
    assert state["reviewed_snapshot"] is None


def test_unexpected_on_start_exception_also_closes_the_run(repo):
    def broken(_run_id):
        raise RuntimeError("sensitive callback detail")

    config = replace(cfg(repo, FakeAgent([]), None), on_start=broken)
    out = run(config)
    state = log_of(repo, out).read_json("state.json")
    assert (out.status, out.calls_used, out.reason) == ("failed", 0, "internal_error:RuntimeError")
    assert state["stage"] == "closed" and state["status"] == "failed"


def test_critic_packet_does_not_read_a_changed_symlinks_external_target(repo):
    private = repo / "private-local.txt"
    private.write_text("PRIVATE-EXTERNAL-CONTENT")

    def link(request):
        try:
            (request.cwd / "linked.txt").symlink_to(private)
        except OSError:
            pytest.skip("o sistema não permite criar symlink neste teste")
        return AgentResult("ok")

    critic = FakeAgent([review()])
    out = run(cfg(repo, FakeAgent([link]), critic))
    assert out.status == "done"
    assert "PRIVATE-EXTERNAL-CONTENT" not in critic.requests[0].prompt
    assert "conteúdo do destino não incluído" in critic.requests[0].prompt
    assert private.read_text() == "PRIVATE-EXTERNAL-CONTENT"

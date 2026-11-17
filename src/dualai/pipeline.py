"""A sequência fixa de uma execução e as regras de parada.

autor -> verificação -> crítico -> resolução -> verificação. Não há laço de
revisão: o crítico é chamado uma vez. O teto de chamadas vive aqui, no código.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from dualai.agents import Agent, AgentRequest, AgentResult
from dualai.findings import (
    REVIEW_SCHEMA,
    RESOLUTION_SCHEMA,
    SEVERITIES,
    STATES,
    Finding,
    Resolution,
    close_findings,
    final_status,
    parse_resolution,
    parse_review,
    tests_failed_finding,
    triage,
)
from dualai.modes import code_review
from dualai.proc import Cancelled, CancelToken
from dualai.runlog import RunLog, new_run_id, runs_dir
from dualai.workspace import (
    WorkspaceError,
    changed_files,
    collect_diff,
    create_worktree,
    has_uncommitted_changes,
    require_snapshot,
    run_check,
    snapshot,
    stage_all,
    staged_diff,
    worktree_path,
)

MODES = {"code_review": code_review}

REPAIR_NOTE = """

ATENÇÃO: a sua resposta anterior a este mesmo pedido foi recusada pelo validador: {error}
Responda de novo, somente com o objeto JSON no formato pedido acima."""


@dataclass(frozen=True)
class RunConfig:
    task: str
    repo: Path
    test_cmd: str
    author: Agent
    critic: Agent | None
    author_name: str
    critic_name: str
    mode: str = "code_review"
    call_timeout_s: int = 600
    check_timeout_s: int = 300
    cancel: CancelToken | None = None  # interrupção a pedido do usuário
    on_start: Callable[[str], None] | None = None  # avisado com o id assim que a execução existe


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    status: str
    calls_used: int
    reason: str = ""


class _Stop(Exception):
    def __init__(self, status: str, reason: str = ""):
        super().__init__(reason)
        self.status, self.reason = status, reason


def _unique_run_id(repo: Path, base: str) -> str:
    candidate, number = base, 1
    while (runs_dir(repo) / candidate).exists() or worktree_path(repo, candidate).exists():
        number += 1
        candidate = f"{base}-{number}"
    return candidate


def _read_files(worktree: Path, names: list[str]) -> dict[str, str]:
    files = {}
    for name in names:
        path = worktree / name
        if path.is_symlink() or not path.resolve().is_relative_to(worktree.resolve()):
            files[name] = "(link simbólico: conteúdo do destino não incluído)"
        elif path.is_file():
            files[name] = path.read_bytes().decode("utf-8", errors="replace")
        else:
            files[name] = "(arquivo removido)"
    return files


def _backlog_md(backlog: list[Finding]) -> str:
    lines = ["# Backlog desta execução", "", "Itens que não bloqueiam a conclusão.", ""]
    for finding in backlog:
        place = finding.file + (f":{finding.line}" if finding.line is not None else "")
        demoted = f" (rebaixado de {finding.demoted_from}: faltou arquivo, linha ou cenário)" if finding.demoted_from else ""
        lines.append(f"- [{finding.id}] ({finding.severity}{demoted}) {finding.title}" + (f" — {place}" if place else ""))
        for label, value in (("Cenário", finding.scenario), ("Sugestão", finding.suggestion)):
            if value:
                lines.append(f"  - {label}: {value}")
    if not backlog:
        lines.append("Nenhum item.")
    return "\n".join(lines) + "\n"


def _summary_md(config: RunConfig, run_id: str, status: str, reason: str, calls_used: int,
                tests_pass: bool | None, uncommitted: bool, blocking: list[Finding],
                resolutions: list[Resolution], backlog: list[Finding]) -> str:
    tests = {None: "não foram rodados", True: "passaram", False: "FALHARAM"}[tests_pass]
    critic = config.critic_name if config.critic is not None else "nenhum (modo solo)"
    lines = [
        f"# Execução {run_id}",
        "",
        f"- Estado: **{status}**" + (f" ({reason})" if reason else ""),
        f"- Tarefa: {config.task.strip().splitlines()[0] if config.task.strip() else ''}",
        f"- Autor: {config.author_name} · Crítico: {critic} · Chamadas a modelo: {calls_used}",
        f"- Testes (`{config.test_cmd}`): {tests}",
    ]
    if uncommitted:
        lines.append("- Aviso: as suas alterações não commitadas não foram incluídas; a execução partiu do último commit.")
    lines += ["", "## Achados bloqueantes", ""]
    notes = {resolution.finding_id: resolution for resolution in resolutions}
    for finding in blocking:
        resolution = notes.get(finding.id)
        state = resolution.state if resolution else "sem estado final"
        place = finding.file + (f":{finding.line}" if finding.line is not None else "")
        lines.append(f"- [{finding.id}] ({finding.severity}) {finding.title} — {place} — **{state}**")
        if resolution and resolution.note:
            lines.append(f"  - Nota do autor: {resolution.note}")
    if not blocking:
        lines.append("Nenhum.")
    lines += ["", f"Itens não bloqueantes: {len(backlog)} (ver `backlog.md`).", "", "## Próximo passo", ""]
    worktree = worktree_path(Path(os.path.abspath(config.repo)), run_id)
    lines.append(f"- O resultado está em `{worktree}`; o seu branch não foi alterado.")
    lines.append(f"- Aceitar: `dualai approve {run_id}` · Rejeitar: `dualai discard {run_id}`")
    if status == "needs_decision":
        lines.append("- Esta execução pede a sua decisão: leia os itens `escalated` e a saída dos testes antes de aprovar.")
    return "\n".join(lines) + "\n"


def run(config: RunConfig, now: datetime | None = None) -> RunOutcome:
    if config.mode not in MODES:
        raise ValueError(f"modalidade desconhecida: {config.mode!r}; disponíveis: {sorted(MODES)}")
    mode = MODES[config.mode]
    repo = Path(os.path.abspath(config.repo))  # os agentes recebem sempre um caminho absoluto

    run_id = _unique_run_id(repo, new_run_id(config.task, now or datetime.now()))
    worktree = create_worktree(repo, run_id)
    uncommitted = has_uncommitted_changes(repo)
    log = RunLog(repo, run_id)
    log.write_text("task.md", config.task)
    log.set_state(
        status="running", mode=config.mode, author=config.author_name,
        critic=config.critic_name if config.critic is not None else "none",
        uncommitted_ignored=uncommitted, calls_used=0, test_cmd=config.test_cmd,
    )
    calls = {"used": 0, "repair_used": False}
    blocking: list[Finding] = []
    backlog: list[Finding] = []
    resolutions: list[Resolution] = []
    tests_pass: bool | None = None
    reason = ""

    def enter(stage: str) -> None:
        # Registra a etapa que vai começar. Para aqui se a interrupção já foi pedida.
        if config.cancel is not None and config.cancel.is_set():
            raise Cancelled()
        log.set_state(stage=stage)

    def invoke(role: str, agent: Agent, name: str, request: AgentRequest) -> AgentResult:
        if config.cancel is not None and config.cancel.is_set():
            raise Cancelled()
        calls["used"] += 1
        log.set_state(calls_used=calls["used"])
        result = agent(request)
        log.record_call(role, name, result)
        if result.status == "limit_reached":
            raise _Stop("limit_reached", result.detail or "limite de uso do provedor")
        if result.status != "ok":
            raise _Stop("failed", f"{result.status}: {result.detail}".rstrip(": "))
        return result

    def invoke_parsed(role: str, agent: Agent, name: str, request: AgentRequest, parse):
        """Chama e valida. Resposta inválida dá direito a um único reparo por execução."""
        result = invoke(role, agent, name, request)
        try:
            return parse(result.data)
        except ValueError as error:
            if calls["repair_used"]:
                raise _Stop("failed", "invalid_output") from None
            calls["repair_used"] = True
            repair = replace(request, prompt=request.prompt + REPAIR_NOTE.format(error=error))
        result = invoke(role, agent, name, repair)
        try:
            return parse(result.data)
        except ValueError:
            raise _Stop("failed", "invalid_output") from None

    try:
        if config.on_start is not None:
            config.on_start(run_id)
        # Chamada 1: o autor implementa.
        enter("author")
        first = invoke("author", config.author, config.author_name, AgentRequest(
            mode.producer_prompt(config.task), worktree, "write", None, config.call_timeout_s, config.cancel))
        log.write_text("author.md", first.text)
        names = changed_files(worktree)
        if not names:
            raise _Stop("failed", "no_changes")
        diff = collect_diff(worktree)
        enter("check")
        check = run_check(worktree, config.test_cmd, config.check_timeout_s, config.cancel)
        tests_pass = check.passed
        log.set_state(tests_1_pass=check.passed)
        # run_check restaura o snapshot anterior, inclusive se os testes usaram git add.
        log.write_text("diff-initial.patch", diff)
        log.write_text("diff.patch", diff)
        log.write_text("tests-1.txt", check.output)

        if config.critic is not None:
            # Chamada 2: o crítico revisa uma única vez, com um pacote autossuficiente.
            try:
                packet = mode.critic_prompt(config.task, diff, _read_files(worktree, names), check.output)
            except mode.PacketTooLarge:
                raise _Stop("needs_decision", "packet_too_large") from None
            enter("critic")
            findings = invoke_parsed("critic", config.critic, config.critic_name, AgentRequest(
                packet, worktree, "read", REVIEW_SCHEMA, config.call_timeout_s, config.cancel), parse_review)
            blocking, backlog = triage(findings)
            log.write_json("review.json", {
                "findings": [asdict(finding) for finding in findings],
                "blocking": [asdict(finding) for finding in blocking],
                "backlog": [asdict(finding) for finding in backlog],
            })
            log.write_text("backlog.md", _backlog_md(backlog))
            if not check.passed:
                blocking = [tests_failed_finding(check.output), *blocking]

            if blocking:
                # Chamada 3: o autor resolve, justifica ou encaminha cada bloqueante.
                pending = list(blocking)
                enter("resolve")
                resolutions = invoke_parsed("author", config.author, config.author_name, AgentRequest(
                    mode.resolve_prompt(config.task, pending, check.output), worktree, "write",
                    RESOLUTION_SCHEMA, config.call_timeout_s, config.cancel),
                    lambda data: parse_resolution(data, pending))
                stage_all(worktree)  # antes dos testes: o que eles gerarem fica fora do commit
                enter("recheck")
                second = run_check(worktree, config.test_cmd, config.check_timeout_s, config.cancel)
                tests_pass = second.passed
                log.write_text("tests-2.txt", second.output)
                resolutions = close_findings(blocking, resolutions)
                log.write_json("resolution.json", {
                    "resolutions": [asdict(resolution) for resolution in resolutions],
                    "tests_pass": tests_pass,
                })

        status = final_status(resolutions, bool(tests_pass))
    except _Stop as stop:
        status, reason = stop.status, stop.reason
    except WorkspaceError as error:
        status, reason = "failed", str(error)
    except (KeyboardInterrupt, Cancelled):
        status, reason = "cancelled", "interrompido pelo usuário"
    except Exception as error:
        # Mensagens arbitrárias de exceção podem conter conteúdo do projeto ou credenciais.
        status, reason = "failed", f"internal_error:{type(error).__name__}"

    reviewed = None
    if status in ("done", "needs_decision"):
        try:
            expected = snapshot(worktree)
            require_snapshot(worktree, expected)
            log.write_text("diff.patch", staged_diff(worktree))
            reviewed = asdict(expected)
        except WorkspaceError as error:
            status, reason = "failed", str(error)
        except Exception as error:
            status, reason = "failed", f"internal_error:{type(error).__name__}"

    severity_count = Counter(finding.severity for finding in [*blocking, *backlog] if finding.id != "F0")
    state_count = Counter(resolution.state for resolution in resolutions)
    try:
        log.update_metrics(
            calls=log.read_json("metrics.json").get("calls", []),
            author=config.author_name,
            critic=config.critic_name if config.critic is not None else "none",
            status=status,
            findings={severity: severity_count.get(severity, 0) for severity in SEVERITIES},
            resolutions={state: state_count.get(state, 0) for state in STATES},
            decisions_requested=1 if status == "needs_decision" else 0,
        )
        log.write_text("summary.md", _summary_md(
            config, run_id, status, reason, calls["used"], tests_pass, uncommitted, blocking, resolutions, backlog))
        log.set_state(status=status, reason=reason, calls_used=calls["used"], tests_pass=tests_pass,
                      stage="closed", reviewed_snapshot=reviewed)
    except Exception as error:
        status, reason = "failed", f"internal_error:{type(error).__name__}"
        # Se o armazenamento ainda aceita escrita, a execução não fica falsamente running.
        log.set_state(status=status, reason=reason, calls_used=calls["used"], tests_pass=tests_pass,
                      stage="closed", reviewed_snapshot=None)
    return RunOutcome(run_id, status, calls["used"], reason)

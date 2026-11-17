"""Decisões do usuário sobre uma execução: aprovar ou descartar.

Usadas pelo terminal e pela interface, para que as duas portas tenham
exatamente as mesmas regras.
"""

from __future__ import annotations

from datetime import date
from dataclasses import asdict
from pathlib import Path

from dualai.runlog import RunLog
from dualai.workspace import WorkspaceSnapshot, approve, discard, worktree_path

APPROVABLE = ("done", "needs_decision")


class ActionError(Exception):
    pass


def require_run(repo: Path, run_id: str) -> RunLog:
    """Devolve o registro da execução. Recusa ids que não sejam um nome simples de pasta."""
    if not run_id or run_id in (".", "..") or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
        raise ActionError(f"identificador de execução inválido: {run_id!r}")
    log = RunLog(Path(repo), run_id)
    if not (log.dir / "state.json").exists():
        raise ActionError(f"execução não encontrada: {run_id}")
    return log


def _require_worktree(repo: Path, run_id: str) -> Path:
    worktree = worktree_path(repo, run_id)
    if not worktree.is_dir():
        raise ActionError(f"a execução {run_id} não tem mais cópia isolada (já foi aprovada ou descartada)")
    return worktree


def approve_run(repo: Path, run_id: str) -> None:
    """Registra a decisão em docs/decisions.md e leva o resultado ao branch atual do usuário."""
    repo = Path(repo)
    log = require_run(repo, run_id)
    state = log.read_json("state.json")
    status = state.get("status")
    if state.get("decision"):
        raise ActionError("a execução já recebeu uma decisão")
    if status not in APPROVABLE:
        raise ActionError(
            f"a execução terminou em {status}; só dá para aprovar {' ou '.join(APPROVABLE)}. "
            f"O que foi feito está em {worktree_path(repo, run_id)}; use discard para limpar.")
    worktree = _require_worktree(repo, run_id)
    pending = state.get("approval_snapshot")
    raw_snapshot = pending or state.get("reviewed_snapshot")
    try:
        expected = WorkspaceSnapshot(**raw_snapshot)
    except (TypeError, ValueError):
        raise ActionError("a execução não tem snapshot revisado; prepare uma nova execução antes de aprovar") from None
    task = (log.dir / "task.md").read_text(encoding="utf-8").strip()
    first_line = task.splitlines()[0] if task else run_id

    def record_decision() -> None:
        decisions = worktree / "docs" / "decisions.md"
        if (decisions.parent.is_symlink() or decisions.is_symlink() or
                not decisions.resolve().is_relative_to(worktree.resolve())):
            raise ActionError("docs/decisions.md precisa ser um arquivo regular dentro da cópia isolada")
        decisions.parent.mkdir(parents=True, exist_ok=True)
        recorded = decisions.read_text(encoding="utf-8") if decisions.exists() else "# Decisões\n\n"
        if run_id not in recorded:
            line = f"- {date.today().isoformat()} · {run_id} · {first_line}\n"
            decisions.write_text(recorded + line, encoding="utf-8")

    approve(repo, run_id, f"dualai: {first_line}"[:72],
            also_stage=() if pending else ("docs/decisions.md",), expected=expected,
            prepare=None if pending else record_decision,
            on_commit=lambda prepared: log.set_state(approval_snapshot=asdict(prepared)))
    log.set_state(decision="approved")


def discard_run(repo: Path, run_id: str) -> None:
    repo = Path(repo)
    log = require_run(repo, run_id)
    state = log.read_json("state.json")
    if state.get("status") == "running":
        raise ActionError("a execução ainda está em andamento; interrompa e aguarde antes de descartar")
    if state.get("decision"):
        raise ActionError("a execução já recebeu uma decisão")
    _require_worktree(repo, run_id)
    discard(repo, run_id)
    log.set_state(decision="discarded")

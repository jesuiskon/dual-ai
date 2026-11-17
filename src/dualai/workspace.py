"""Worktree git isolado de uma execução: criar, medir o diff, verificar, aprovar, descartar."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from dualai.proc import run_process

EXCLUDE_LINE = ".dualai/"


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    output: str
    seconds: float


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Referências e árvore imutável do índice; não é uma sandbox de processos."""

    head: str
    tree: str
    branch: str


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, NotADirectoryError) as error:
        raise WorkspaceError(f"não foi possível rodar git em {cwd}: {error}") from None
    if check and done.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} falhou: {done.stderr.strip() or done.stdout.strip()}")
    return done


def worktree_path(repo: Path, run_id: str) -> Path:
    return Path(repo) / ".dualai" / "worktrees" / run_id


def _branch(run_id: str) -> str:
    return f"dualai/{run_id}"


def require_repo_root(repo: Path) -> None:
    if not repo.is_dir():
        raise WorkspaceError(f"a pasta não existe: {repo}")
    top = _git(repo, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        raise WorkspaceError(f"a pasta não é um repositório git: {repo}")
    if Path(top.stdout.strip()).resolve() != repo.resolve():
        raise WorkspaceError(f"--repo precisa ser a raiz do repositório git: {top.stdout.strip()}")
    if _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
        raise WorkspaceError(f"o repositório ainda não tem commits: {repo}")


def _exclude_dualai(repo: Path) -> None:
    common = Path(_git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    exclude = (common if common.is_absolute() else repo / common) / "info" / "exclude"
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if EXCLUDE_LINE not in current.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        separator = "" if current == "" or current.endswith("\n") else "\n"
        exclude.write_text(current + separator + EXCLUDE_LINE + "\n", encoding="utf-8")


def create_worktree(repo: Path, run_id: str) -> Path:
    repo = Path(repo)
    require_repo_root(repo)
    _exclude_dualai(repo)
    path = worktree_path(repo, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", _branch(run_id), str(path), "HEAD")
    return path


def has_uncommitted_changes(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain").stdout.strip() != ""


def stage_all(worktree: Path) -> None:
    """Põe no índice o estado atual do worktree. O `approve` leva só o que está no índice."""
    _git(worktree, "add", "-A")


def snapshot(worktree: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        head=_git(worktree, "rev-parse", "HEAD").stdout.strip(),
        tree=_git(worktree, "write-tree").stdout.strip(),
        branch=_git(worktree, "symbolic-ref", "--quiet", "HEAD", check=False).stdout.strip(),
    )


def reset_to_staged(worktree: Path, expected: WorkspaceSnapshot | None = None) -> None:
    """Desfaz o que não está no índice: sobras e alterações deixadas pelo comando de testes."""
    if expected is not None:
        current_head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        current_branch = _git(worktree, "symbolic-ref", "--quiet", "HEAD", check=False).stdout.strip()
        if (current_head, current_branch) != (expected.head, expected.branch):
            raise WorkspaceError("o comando de testes alterou HEAD ou o branch; cópia preservada para inspeção")
        # O comando pode ter executado git add. Nunca confiar no índice deixado por ele.
        _git(worktree, "read-tree", "--reset", expected.tree)
    _git(worktree, "restore", "--worktree", ".")
    _git(worktree, "clean", "-fd")


def require_snapshot(worktree: Path, expected: WorkspaceSnapshot) -> None:
    """Recusa conteúdo alterado desde a revisão, sem adicionar nada ao índice."""
    if snapshot(worktree) != expected:
        raise WorkspaceError("o índice, HEAD ou branch mudou após a revisão; resultado não aprovado")
    if (_git(worktree, "diff", "--quiet", "--", check=False).returncode != 0 or
            _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").stdout):
        raise WorkspaceError("há arquivos alterados ou novos após a revisão; resultado não aprovado")


def staged_diff(worktree: Path) -> str:
    """Lê o patch do índice sem incorporar arquivos produzidos pelos testes."""
    return _git(worktree, "diff", "--cached", "HEAD").stdout


def collect_diff(worktree: Path) -> str:
    stage_all(worktree)
    return staged_diff(worktree)


def changed_files(worktree: Path) -> list[str]:
    stage_all(worktree)
    names = _git(worktree, "diff", "--cached", "--name-only", "HEAD").stdout.splitlines()
    return sorted(name.strip() for name in names if name.strip())


def run_check(worktree: Path, test_cmd: str, timeout_s: int, cancel=None) -> CheckResult:
    expected = snapshot(worktree)
    started = time.monotonic()
    try:
        try:
            done = run_process(test_cmd, shell=True, cwd=worktree, timeout=timeout_s, cancel=cancel)
            passed = done.returncode == 0
            output = (done.stdout or "") + (done.stderr or "")
        except subprocess.TimeoutExpired as expired:
            passed = False
            output = (expired.output or "") + (expired.stderr or "")
            output += f"\n[dualai] timeout: o comando de testes passou de {timeout_s}s e foi interrompido\n"
    finally:
        reset_to_staged(worktree, expected)
    return CheckResult(passed, output, round(time.monotonic() - started, 2))


def _remove(repo: Path, run_id: str) -> None:
    path = worktree_path(repo, run_id)
    if path.exists():
        if _git(repo, "worktree", "remove", "--force", str(path), check=False).returncode != 0:
            shutil.rmtree(path, ignore_errors=True)
    _git(repo, "worktree", "prune", check=False)
    _git(repo, "branch", "-D", _branch(run_id), check=False)


def _require_run(repo: Path, run_id: str) -> Path:
    path = worktree_path(repo, run_id)
    if not path.is_dir():
        raise WorkspaceError(f"não há worktree para a execução {run_id!r}")
    return path


def approve(repo: Path, run_id: str, message: str, also_stage: tuple[str, ...] = (),
            expected: WorkspaceSnapshot | None = None,
            prepare: Callable[[], None] | None = None,
            on_commit: Callable[[WorkspaceSnapshot], None] | None = None) -> None:
    """Commit do que está no índice do worktree, merge fast-forward no branch do usuário, limpeza.

    Só entra o que foi preparado com `stage_all` (mais os caminhos de `also_stage`). Arquivos
    que o comando de testes deixou para trás ficam de fora.
    """
    repo = Path(repo)
    worktree = _require_run(repo, run_id)
    if expected is not None:
        require_snapshot(worktree, expected)
    if prepare is not None:
        prepare()
    if also_stage:
        _git(worktree, "add", "--", *also_stage)
    commit_tree = snapshot(worktree).tree
    if _git(worktree, "diff", "--cached", "--quiet", "HEAD", check=False).returncode != 0:
        _git(worktree, "commit", "-m", message)
    prepared = snapshot(worktree)
    if prepared.tree != commit_tree:
        raise WorkspaceError("o commit alterou o conteúdo preparado; cópia preservada para inspeção")
    if expected is not None:
        require_snapshot(worktree, prepared)
    if on_commit is not None:
        on_commit(prepared)
    merge = _git(repo, "merge", "--ff-only", _branch(run_id), check=False)
    if merge.returncode != 0:
        raise WorkspaceError(
            "o seu branch mudou desde o início da execução (ou há alterações locais em conflito); "
            f"nada foi alterado. Detalhe do git: {merge.stderr.strip() or merge.stdout.strip()}"
        )
    _remove(repo, run_id)


def discard(repo: Path, run_id: str) -> None:
    repo = Path(repo)
    _require_run(repo, run_id)
    _remove(repo, run_id)

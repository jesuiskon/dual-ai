"""Camada de serviço da interface: tudo o que a página pode pedir, em funções Python.

Não há lógica de orquestração aqui. Uma execução é `pipeline.run` em uma
thread; o que a página mostra é lido da pasta da execução; aprovar e descartar
são as mesmas funções do terminal.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from dualai.actions import APPROVABLE, ActionError, approve_run, discard_run, require_run
from dualai.agents import fake_author, fake_critic, run_claude, run_codex
from dualai.pipeline import RunConfig, run
from dualai.proc import Cancelled, CancelToken
from dualai.runlog import RunLog, runs_dir
from dualai.workspace import WorkspaceError, require_repo_root, worktree_path

MAX_TASK_CHARS = 20_000
MAX_TEST_CMD_CHARS = 2_000
MAX_OUTPUT_CHARS = 20_000
MAX_DIFF_CHARS = 300_000
MAX_RECENT = 8
START_TIMEOUT_S = 60
SHUTDOWN_TIMEOUT_S = 30

# Roteiro fixo do seletor de pastas nativo. Roda em um processo à parte porque
# o tkinter precisa da thread principal.
FOLDER_DIALOG = """
import sys, tkinter, tkinter.filedialog
sys.stdout.reconfigure(encoding="utf-8")
root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
print(tkinter.filedialog.askdirectory(title="Escolha a pasta do repositório git", mustexist=True) or "")
"""


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _native_folder_dialog() -> str:
    done = subprocess.run([sys.executable, "-c", FOLDER_DIALOG], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    return done.stdout.strip()


def _paced(agent, delay_s: float):
    """Agente de ensaio com uma pausa antes de responder, para as etapas poderem ser vistas."""
    def paced(request):
        if delay_s > 0:
            if request.cancel is not None:
                if request.cancel.wait(delay_s):
                    raise Cancelled()
            else:
                time.sleep(delay_s)
        return agent(request)
    return paced


def _read(log: RunLog, name: str, limit: int, keep: str = "tail") -> str | None:
    path = log.dir / name
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    if keep == "tail":
        return "[início omitido]\n" + text[-limit:]
    return text[:limit] + "\n[restante omitido]"


def _first_line(text: str | None, fallback: str) -> str:
    stripped = (text or "").strip()
    return stripped.splitlines()[0][:120] if stripped else fallback


class Service:
    def __init__(self, config_path: Path, fake_delay_s: float = 1.0, agents: dict | None = None, picker=None):
        self._config_path = Path(config_path)
        self._fake_delay_s = fake_delay_s
        self._agents = agents or {"author": ("claude", run_claude), "critic": ("codex", run_codex)}
        self._picker = picker or _native_folder_dialog
        self._lock = threading.Lock()
        self._active: dict | None = None  # repo, run_id, token, thread

    # --- configuração da interface -------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        repos = data.get("repos") if isinstance(data.get("repos"), dict) else {}
        recent = [path for path in data.get("recent", []) if isinstance(path, str) and path in repos]
        current = data.get("current") if data.get("current") in repos else None
        return {"repos": repos, "recent": recent, "current": current}

    def _save(self, config: dict) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _known_repo(self, repo) -> str:
        key = os.path.abspath(repo) if isinstance(repo, str) and repo.strip() else ""
        if key not in self._load()["repos"]:
            raise ServiceError(400, "repositório não selecionado nesta interface")
        return key

    def state(self) -> dict:
        config = self._load()

        def entry(path: str) -> dict:
            return {"path": path, "name": Path(path).name, "test_cmd": config["repos"][path].get("test_cmd", "")}

        with self._lock:
            active = self._active
            running = {"repo": active["repo"], "run_id": active["run_id"]} if active else None
            if active and active["run_id"] is None:
                running.update(status="preparing", interrupting=active["token"].is_set())
        return {
            "current": entry(config["current"]) if config["current"] else None,
            "recent": [entry(path) for path in config["recent"]],
            "active": running,
        }

    def select_repo(self, path) -> dict:
        if not isinstance(path, str) or not path.strip():
            raise ServiceError(400, "informe a pasta do repositório")
        key = os.path.abspath(path.strip())
        try:
            require_repo_root(Path(key))
        except WorkspaceError as error:
            raise ServiceError(400, str(error)) from None
        config = self._load()
        config["repos"].setdefault(key, {"test_cmd": ""})
        config["recent"] = [key, *[other for other in config["recent"] if other != key]][:MAX_RECENT]
        config["current"] = key
        self._save(config)
        return self.state()

    def pick_repo(self) -> dict:
        chosen = self._picker()
        if not chosen:
            return {**self.state(), "picked": False}
        return {**self.select_repo(chosen), "picked": True}

    def save_settings(self, repo, test_cmd) -> dict:
        key = self._known_repo(repo)
        if not isinstance(test_cmd, str) or len(test_cmd) > MAX_TEST_CMD_CHARS:
            raise ServiceError(400, "comando de testes inválido")
        config = self._load()
        config["repos"][key]["test_cmd"] = test_cmd.strip()
        self._save(config)
        return self.state()

    # --- leitura das execuções ------------------------------------------------

    def _is_active(self, key: str, run_id: str) -> bool:
        with self._lock:
            active = self._active
            return active is not None and active["repo"] == key and active["run_id"] == run_id

    def _status(self, key: str, run_id: str, state: dict) -> str:
        status = state.get("status", "failed")
        if status == "running" and not self._is_active(key, run_id):
            return "interrupted"  # ficou "running" no disco, mas não há execução viva neste processo
        return status

    def list_runs(self, repo) -> list[dict]:
        key = self._known_repo(repo)
        base = runs_dir(Path(key))
        folders = sorted((path for path in base.iterdir() if path.is_dir()), reverse=True) if base.is_dir() else []
        runs = []
        for folder in folders:
            log = RunLog(Path(key), folder.name)
            state = log.read_json("state.json")
            if not state:
                continue
            runs.append({
                "id": folder.name,
                "title": _first_line(_read(log, "task.md", 2_000, keep="head"), folder.name),
                "status": self._status(key, folder.name, state),
                "decision": state.get("decision"),
                "calls_used": state.get("calls_used", 0),
                "fake": state.get("author") == "fake",
            })
        return runs

    def run_detail(self, repo, run_id) -> dict:
        key = self._known_repo(repo)
        try:
            log = require_run(Path(key), run_id if isinstance(run_id, str) else "")
        except ActionError as error:
            raise ServiceError(404, str(error)) from None
        state = log.read_json("state.json")
        status = self._status(key, run_id, state)
        diff = _read(log, "diff.patch", MAX_DIFF_CHARS, keep="head")
        review = log.read_json("review.json") if (log.dir / "review.json").exists() else None
        resolution = log.read_json("resolution.json") if (log.dir / "resolution.json").exists() else None
        calls = log.read_json("metrics.json").get("calls", [])
        has_worktree = worktree_path(Path(key), run_id).is_dir()
        decision = state.get("decision")
        with self._lock:
            active = self._active
            interrupting = bool(active and active["run_id"] == run_id and active["token"].is_set())
        return {
            "id": run_id,
            "status": status,
            "stage": state.get("stage"),
            "reason": state.get("reason", ""),
            "task": _read(log, "task.md", MAX_TASK_CHARS, keep="head") or "",
            "author": state.get("author"),
            "critic": state.get("critic"),
            "fake": state.get("author") == "fake",
            "calls_used": state.get("calls_used", 0),
            "seconds": round(sum(float(call.get("seconds") or 0) for call in calls), 1),
            "tests_pass": state.get("tests_pass"),
            "tests_1_pass": state.get("tests_1_pass"),
            "decision": decision,
            "uncommitted_ignored": bool(state.get("uncommitted_ignored")),
            "test_cmd": state.get("test_cmd", ""),
            "author_text": _read(log, "author.md", MAX_OUTPUT_CHARS, keep="head"),
            "files": re.findall(r"^diff --git a/.+ b/(.+)$", diff or "", flags=re.MULTILINE),
            "diff": diff,
            "tests_1": _read(log, "tests-1.txt", MAX_OUTPUT_CHARS),
            "tests_2": _read(log, "tests-2.txt", MAX_OUTPUT_CHARS),
            "review": {"blocking": review.get("blocking", []), "backlog": review.get("backlog", [])} if review else None,
            "resolution": resolution,
            "can_decide": status in APPROVABLE and not decision and has_worktree,
            "can_discard": state.get("status") != "running" and not decision and has_worktree,
            "recovery_required": status == "interrupted" and state.get("status") == "running",
            "interrupting": interrupting,
        }

    # --- ações ---------------------------------------------------------------

    def start_run(self, repo, task, fake: bool = False) -> dict:
        key = self._known_repo(repo)
        task = task.strip() if isinstance(task, str) else ""
        if not task:
            raise ServiceError(400, "escreva a tarefa antes de enviar")
        if len(task) > MAX_TASK_CHARS:
            raise ServiceError(400, f"a tarefa passa de {MAX_TASK_CHARS} caracteres")
        test_cmd = self._load()["repos"][key].get("test_cmd", "").strip()
        if not test_cmd:
            raise ServiceError(400, "defina o comando de testes deste repositório antes de enviar tarefas")

        if fake:
            author = ("fake", _paced(fake_author, self._fake_delay_s))
            critic = ("fake", _paced(fake_critic, self._fake_delay_s))
        else:
            author, critic = self._agents["author"], self._agents["critic"]

        entry = {"repo": key, "run_id": None, "token": CancelToken(), "thread": None}
        with self._lock:
            if self._active is not None:
                raise ServiceError(409, "já existe uma execução em andamento")
            self._active = entry
        started = threading.Event()
        accepted = threading.Event()
        failure: dict = {}

        def on_start(run_id: str) -> None:
            with self._lock:
                entry["run_id"] = run_id
            started.set()
            # Não iniciar um agente se o pedido de início já expirou. A resposta
            # decide entre aceitar e cancelar antes de liberar o pipeline.
            accepted.wait()

        def work() -> None:
            try:
                run(RunConfig(task=task, repo=Path(key), test_cmd=test_cmd, author=author[1], critic=critic[1],
                              author_name=author[0], critic_name=critic[0], cancel=entry["token"], on_start=on_start))
            except Exception as error:  # a thread nunca pode morrer em silêncio
                failure["message"] = str(error) or type(error).__name__
            finally:
                with self._lock:
                    if self._active is entry:
                        self._active = None
                started.set()

        entry["thread"] = threading.Thread(target=work, name="dualai-run", daemon=True)
        entry["thread"].start()
        try:
            if not started.wait(START_TIMEOUT_S):
                entry["token"].cancel()
                raise ServiceError(
                    504, "o início demorou demais; a interrupção foi solicitada. "
                    "Aguarde a preparação terminar antes de tentar novamente.")
            if entry["run_id"] is None:
                raise ServiceError(400, failure.get("message") or "não foi possível iniciar a execução")
        finally:
            accepted.set()
        return {"run_id": entry["run_id"]}

    def interrupt(self) -> dict:
        with self._lock:
            active = self._active
        if active is None:
            return {"interrupting": False}
        active["token"].cancel()
        return {"interrupting": True}

    def _decide(self, action, repo, run_id) -> dict:
        key = self._known_repo(repo)
        if self._is_active(key, run_id):
            raise ServiceError(409, "a execução ainda está em andamento; interrompa antes")
        try:
            action(Path(key), run_id if isinstance(run_id, str) else "")
        except (ActionError, WorkspaceError) as error:
            raise ServiceError(400, str(error)) from None
        return self.run_detail(key, run_id)

    def approve(self, repo, run_id) -> dict:
        return self._decide(approve_run, repo, run_id)

    def discard(self, repo, run_id) -> dict:
        return self._decide(discard_run, repo, run_id)

    def shutdown(self) -> None:
        """Interrompe a execução ativa, se houver, e espera ela registrar o estado final."""
        with self._lock:
            active = self._active
        if active is not None:
            active["token"].cancel()
            if active["thread"] is not None:
                active["thread"].join(SHUTDOWN_TIMEOUT_S)

"""Pasta de uma execução: identificador, estado e métricas, tudo em arquivos."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from dualai.agents import AgentResult

SLUG_MAX = 30


def new_run_id(task: str, now: datetime) -> str:
    plain = unicodedata.normalize("NFKD", task).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:SLUG_MAX].strip("-")
    return f"{now:%Y%m%d-%H%M%S}-{slug or 'tarefa'}"


def _retry(action, attempts: int = 50, delay_s: float = 0.01):
    # No Windows, trocar ou abrir um arquivo que outra thread está usando falha por um instante.
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def runs_dir(repo: Path) -> Path:
    return Path(repo) / ".dualai" / "runs"


class RunLog:
    def __init__(self, repo: Path, run_id: str):
        self.run_id = run_id
        self.dir = runs_dir(repo) / run_id

    def write_text(self, name: str, text: str) -> None:
        # Grava em um arquivo temporário e troca de uma vez: quem lê nunca vê o arquivo pela metade.
        self.dir.mkdir(parents=True, exist_ok=True)
        temp = self.dir / f".{name}.{os.getpid()}.{threading.get_ident()}.tmp"
        temp.write_bytes(text.encode("utf-8"))
        _retry(lambda: os.replace(temp, self.dir / name))

    def write_json(self, name: str, obj) -> None:
        self.write_text(name, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")

    def read_json(self, name: str) -> dict:
        path = self.dir / name
        if not path.exists():
            return {}
        return json.loads(_retry(lambda: path.read_text(encoding="utf-8")))

    def set_state(self, **fields) -> None:
        self.write_json("state.json", {**self.read_json("state.json"), **fields})

    def update_metrics(self, **fields) -> None:
        self.write_json("metrics.json", {**self.read_json("metrics.json"), **fields})

    def record_call(self, role: str, agent: str, result: AgentResult) -> None:
        calls = self.read_json("metrics.json").get("calls", [])
        calls.append({
            "role": role,
            "agent": agent,
            "status": result.status,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "seconds": result.seconds,
        })
        self.update_metrics(calls=calls)


def load_all_metrics(repo: Path) -> list[dict]:
    base = runs_dir(repo)
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.glob("*/metrics.json")):
        found.append(json.loads(path.read_text(encoding="utf-8")))
    return found

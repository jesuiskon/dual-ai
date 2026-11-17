"""Tipos comuns a todos os agentes, o agente falso e os adaptadores das CLIs oficiais.

Os adaptadores só iniciam o executável oficial como subprocesso. Eles nunca
leem, copiam ou gravam tokens e arquivos de credenciais: o login é o que a
própria CLI já guarda. O processo filho recebe apenas uma lista fechada de
variáveis de ambiente.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dualai.proc import run_process


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    cwd: Path
    access: str  # "read" ou "write"
    schema: dict | None
    timeout_s: int
    cancel: object | None = None  # proc.CancelToken, quando a execução pode ser interrompida


@dataclass(frozen=True)
class AgentResult:
    status: str  # "ok", "limit_reached", "timeout" ou "error"
    text: str = ""
    data: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    detail: str = ""


Agent = Callable[[AgentRequest], AgentResult]


class FakeAgent:
    """Reproduz um roteiro fixo. Cada passo é um resultado pronto ou uma função."""

    def __init__(self, script: list[AgentResult | Agent]):
        self._script = list(script)
        self.requests: list[AgentRequest] = []

    def __call__(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        assert self._script, "FakeAgent: o roteiro acabou e houve mais uma chamada"
        step = self._script.pop(0)
        return step(request) if callable(step) else step


def fake_author(request: AgentRequest) -> AgentResult:
    """Autor do modo de ensaio: escreve um arquivo e depois resolve todo achado. Não chama modelo."""
    if request.schema is None:
        target = request.cwd / "DUALAI_FAKE.txt"  # acrescenta uma linha, para todo ensaio ter o que revisar
        previous = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(previous + "linha escrita pelo autor falso\n", encoding="utf-8")
        return AgentResult("ok", text="criei DUALAI_FAKE.txt")
    ids = list(dict.fromkeys(re.findall(r"^\[(F\d+)\] \(", request.prompt, flags=re.MULTILINE)))
    return AgentResult("ok", data={"resolutions": [
        {"finding_id": finding_id, "state": "resolved", "note": "resolvido pelo autor falso"} for finding_id in ids]})


def fake_critic(_request: AgentRequest) -> AgentResult:
    """Crítico do modo de ensaio: devolve sempre os mesmos dois achados. Não chama modelo."""
    return AgentResult("ok", data={"findings": [
        {"severity": "relevante", "title": "Achado de exemplo do crítico falso", "file": "DUALAI_FAKE.txt",
         "line": 1, "scenario": "entrada: nenhuma; obtido: texto de exemplo; esperado: texto de exemplo revisado",
         "suggestion": "ajustar o texto"},
        {"severity": "preferencia", "title": "Preferência de exemplo", "file": "", "line": None,
         "scenario": "", "suggestion": ""},
    ]})


# --- comum aos adaptadores -------------------------------------------------

# `subscription` mantém o login salvo pela CLI; não comprova o método de cobrança.
AUTH_MODES = ("subscription", "api_key")
DETAIL_TAIL = 2000

# Variáveis repassadas ao processo filho, comparadas sem diferenciar maiúsculas.
# Nada de chaves, tokens ou variáveis de sessão de quem chamou.
BASE_ENV = ("PATH", "HOME", "USERPROFILE", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "COMSPEC",
            "PATHEXT", "LANG", "LC_ALL")
PROVIDER_ENV = {
    "codex": ("CODEX_HOME",),
    "claude": ("APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR"),
}
API_KEY_VAR = {"codex": "CODEX_API_KEY", "claude": "ANTHROPIC_API_KEY"}


def child_env(source: dict, auth: str, provider: str = "codex") -> dict:
    allowed = {name.upper() for name in (*BASE_ENV, *PROVIDER_ENV[provider])}
    if auth == "api_key":
        allowed.add(API_KEY_VAR[provider])
    return {name: value for name, value in source.items() if name.upper() in allowed}


def _json_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# --- Codex CLI -------------------------------------------------------------

CODEX_LIMIT = re.compile(r"usage limit|rate limit|\b429\b", re.IGNORECASE)


def resolve_codex_launcher(platform: str, env: dict, exists: Callable[[str], bool], node: str) -> list[str]:
    """No Windows o `codex` do npm é um atalho .cmd; chamamos o node com o script real."""
    if platform != "win32":
        return ["codex"]
    path_value = next((value for name, value in env.items() if name.upper() == "PATH"), "")
    folders = [folder for folder in path_value.split(";") if folder]
    for folder in folders:
        candidate = ntpath.join(folder, "codex.exe")
        if exists(candidate):
            return [candidate]
    for folder in folders:
        script = ntpath.join(folder, "node_modules", "@openai", "codex", "bin", "codex.js")
        if exists(ntpath.join(folder, "codex.cmd")) and exists(script):
            return [node, script]
    raise FileNotFoundError("Codex CLI não encontrado no PATH")


def build_codex_args(request: AgentRequest, schema_file: Path | None, output_file: Path,
                     model: str | None) -> list[str]:
    write = request.access == "write"
    args = [
        "--ask-for-approval", "never",
        "exec",
        "--ignore-user-config",
        "--sandbox", "workspace-write" if write else "read-only",
        "--cd", str(request.cwd),
        "--color", "never",
        "--skip-git-repo-check",
        "--config", 'web_search="disabled"',
    ]
    if write:
        for setting in ("sandbox_workspace_write.exclude_tmpdir_env_var=true",
                        "sandbox_workspace_write.exclude_slash_tmp=true",
                        "sandbox_workspace_write.network_access=false"):
            args += ["--config", setting]
    args.append("--json")
    if schema_file is not None:
        args += ["--output-schema", str(schema_file)]
    args += ["--output-last-message", str(output_file)]
    if model:
        args += ["-m", model]
    args.append("-")
    return args


def _jsonl_events(jsonl_text: str) -> list[dict]:
    events = []
    for line in jsonl_text.splitlines():
        event = _json_object(line.strip()) if line.strip().startswith("{") else None
        if event is not None:
            events.append(event)
    return events


def parse_codex_usage(jsonl_text: str) -> tuple[int, int]:
    tokens_in = tokens_out = 0
    for event in _jsonl_events(jsonl_text):
        usage = event.get("usage") if event.get("type") == "turn.completed" else None
        if isinstance(usage, dict):
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
    return tokens_in, tokens_out


def _codex_error_text(stdout: str, stderr: str) -> str:
    """Texto de erro: stderr mais as mensagens dos eventos de erro. Contagens de tokens ficam fora."""
    parts = [stderr]
    for event in _jsonl_events(stdout):
        if event.get("type") in ("error", "turn.failed"):
            parts.append(json.dumps(event, ensure_ascii=False))
    return "\n".join(part for part in parts if part)


def run_codex(request: AgentRequest, *, model: str | None = None, auth: str = "subscription",
              spawn=run_process, launcher: list[str] | None = None) -> AgentResult:
    started = time.monotonic()

    def elapsed() -> float:
        return round(time.monotonic() - started, 2)

    env = child_env(dict(os.environ), auth, "codex")
    if auth == "api_key" and not any(name.upper() == API_KEY_VAR["codex"] for name in env):
        return AgentResult("error", detail="modo api_key: defina a variável CODEX_API_KEY antes de rodar")
    if launcher is None:
        try:
            launcher = resolve_codex_launcher(
                sys.platform, dict(os.environ), os.path.exists, shutil.which("node") or "node")
        except FileNotFoundError as error:
            return AgentResult("error", detail=str(error))

    with tempfile.TemporaryDirectory(prefix="dualai-codex-") as folder:
        output_file = Path(folder) / "last-message.txt"
        schema_file = None
        if request.schema is not None:
            schema_file = Path(folder) / "schema.json"
            schema_file.write_text(json.dumps(request.schema), encoding="utf-8")
        args = [*launcher, *build_codex_args(request, schema_file, output_file, model)]
        try:
            done = spawn(args, input=request.prompt, capture_output=True, text=True, encoding="utf-8",
                         cwd=str(request.cwd), env=env, timeout=request.timeout_s, cancel=request.cancel)
        except subprocess.TimeoutExpired:
            return AgentResult("timeout", seconds=elapsed(), detail=f"o Codex passou de {request.timeout_s}s")
        except OSError as error:
            return AgentResult("error", seconds=elapsed(), detail=f"não foi possível iniciar o Codex: {error}")
        stdout, stderr = done.stdout or "", done.stderr or ""
        tokens_in, tokens_out = parse_codex_usage(stdout)
        if done.returncode != 0:
            error_text = _codex_error_text(stdout, stderr)
            status = "limit_reached" if CODEX_LIMIT.search(error_text) else "error"
            return AgentResult(status, tokens_in=tokens_in, tokens_out=tokens_out, seconds=elapsed(),
                               detail=error_text[-DETAIL_TAIL:] or f"o Codex saiu com código {done.returncode}")
        message = output_file.read_text(encoding="utf-8") if output_file.exists() else ""

    data = _json_object(message) if request.schema is not None else None
    return AgentResult("ok", text=message, data=data, tokens_in=tokens_in, tokens_out=tokens_out,
                       seconds=elapsed())


# --- Claude Code -----------------------------------------------------------

# Modo oficial `--restricted`: remove as ferramentas que rodam comandos, confina
# as ferramentas de arquivo à pasta de trabalho e ignora os arquivos de
# configuração de usuário, projeto e local. Mantém o login salvo pela CLI
# (verificado em 2026-11-14 com o Claude Code 2.1.319). Nada é desligado nas
# sessões do próprio usuário: o efeito vale só para este processo filho.
CLAUDE_CLEAN_FLAGS = ["--restricted", "--strict-mcp-config", "--no-session-persistence"]

# Freio estimado por chamada, calculado pela CLI; pode ultrapassar o valor.
# Não é orçamento total da tarefa nem comprovação do método real de cobrança.
CLAUDE_MAX_BUDGET_USD = "1.00"

CLAUDE_READ_TOOLS = "Read,Glob,Grep"
CLAUDE_WRITE_TOOLS = "Read,Glob,Grep,Edit,Write"
CLAUDE_LIMIT = re.compile(r"usage limit|rate.?limit|hit your limit|limit reached|\b429\b", re.IGNORECASE)


def build_claude_args(request: AgentRequest, model: str | None, auth: str, extra: list[str]) -> list[str]:
    tools = CLAUDE_WRITE_TOOLS if request.access == "write" else CLAUDE_READ_TOOLS
    args = [
        "-p",
        "--output-format", "json",
        "--permission-mode", "dontAsk",
        "--tools", tools,
        "--allowedTools", tools,
        "--max-budget-usd", CLAUDE_MAX_BUDGET_USD,
        *extra,
    ]
    if auth == "api_key":
        args.append("--bare")
    if request.schema is not None:
        args += ["--json-schema", json.dumps(request.schema)]
    if model:
        args += ["--model", model]
    return args


def _claude_tokens(output: dict) -> tuple[int, int]:
    per_model = output.get("modelUsage")
    if isinstance(per_model, dict) and per_model:
        usages = [usage for usage in per_model.values() if isinstance(usage, dict)]
        keys_in = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        return (sum(int(usage.get(key) or 0) for usage in usages for key in keys_in),
                sum(int(usage.get("outputTokens") or 0) for usage in usages))
    usage = output.get("usage") if isinstance(output.get("usage"), dict) else {}
    keys_in = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return sum(int(usage.get(key) or 0) for key in keys_in), int(usage.get("output_tokens") or 0)


def parse_claude_output(stdout: str) -> AgentResult:
    output = _json_object(stdout.strip())
    if output is None:
        return AgentResult("error", detail=f"saída inesperada do Claude Code: {stdout.strip()[-DETAIL_TAIL:]}")
    if (not isinstance(output.get("is_error"), bool)
            or output.get("type", "result") != "result"
            or (output["is_error"] is False and (
                output.get("subtype", "success") != "success"
                or not (isinstance(output.get("result"), str)
                        or isinstance(output.get("structured_output"), dict))))):
        return AgentResult("error", detail=f"envelope inválido do Claude Code: {stdout.strip()[-DETAIL_TAIL:]}")
    try:
        tokens_in, tokens_out = _claude_tokens(output)
        seconds = round(float(output.get("duration_ms") or 0) / 1000, 2)
    except (TypeError, ValueError, OverflowError):
        return AgentResult("error", detail="métricas inválidas na saída do Claude Code")
    text = output.get("result") if isinstance(output.get("result"), str) else ""
    if output.get("is_error"):
        errors = output.get("errors") if isinstance(output.get("errors"), list) else []
        error_text = "\n".join(str(part) for part in (output.get("subtype"), *errors, text) if part)
        status = "limit_reached" if CLAUDE_LIMIT.search(error_text) else "error"
        return AgentResult(status, text=text, tokens_in=tokens_in, tokens_out=tokens_out, seconds=seconds,
                           detail=error_text[-DETAIL_TAIL:] or "o Claude Code devolveu um erro sem mensagem")
    data = output.get("structured_output") if isinstance(output.get("structured_output"), dict) else None
    return AgentResult("ok", text=text, data=data, tokens_in=tokens_in, tokens_out=tokens_out, seconds=seconds)


def _json_from_text(text: str) -> dict | None:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    return _json_object(fenced.group(1) if fenced else stripped)


def run_claude(request: AgentRequest, *, model: str | None = None, auth: str = "subscription",
               spawn=run_process, executable: str | None = None) -> AgentResult:
    started = time.monotonic()

    def elapsed() -> float:
        return round(time.monotonic() - started, 2)

    env = child_env(dict(os.environ), auth, "claude")
    if auth == "api_key" and not any(name.upper() == API_KEY_VAR["claude"] for name in env):
        return AgentResult("error", detail="modo api_key: defina a variável ANTHROPIC_API_KEY antes de rodar")
    executable = executable or shutil.which("claude")
    if not executable:
        return AgentResult("error", detail="Claude Code não encontrado no PATH")

    args = [executable, *build_claude_args(request, model, auth, CLAUDE_CLEAN_FLAGS)]
    try:
        done = spawn(args, input=request.prompt, capture_output=True, text=True, encoding="utf-8",
                     cwd=str(request.cwd), env=env, timeout=request.timeout_s, cancel=request.cancel)
    except subprocess.TimeoutExpired:
        return AgentResult("timeout", seconds=elapsed(), detail=f"o Claude Code passou de {request.timeout_s}s")
    except OSError as error:
        return AgentResult("error", seconds=elapsed(), detail=f"não foi possível iniciar o Claude Code: {error}")

    stdout, stderr = done.stdout or "", done.stderr or ""
    if _json_object(stdout.strip()) is None:
        error_text = (stdout + "\n" + stderr).strip()
        status = "limit_reached" if CLAUDE_LIMIT.search(error_text) else "error"
        return AgentResult(status, seconds=elapsed(),
                           detail=error_text[-DETAIL_TAIL:] or f"o Claude Code saiu com código {done.returncode}")
    result = parse_claude_output(stdout)
    if done.returncode != 0:
        error_text = "\n".join(part for part in (
            f"o Claude Code saiu com código {done.returncode}", result.detail, result.text, stderr) if part)
        status = "limit_reached" if CLAUDE_LIMIT.search(error_text) else "error"
        return AgentResult(status, text=result.text, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                           seconds=elapsed(), detail=error_text[-DETAIL_TAIL:])
    data = result.data
    if result.status == "ok" and request.schema is not None and data is None:
        data = _json_from_text(result.text)
    return AgentResult(result.status, text=result.text, data=data, tokens_in=result.tokens_in,
                       tokens_out=result.tokens_out, seconds=elapsed(), detail=result.detail)

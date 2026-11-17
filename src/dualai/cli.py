"""Linha de comando: run, status, approve, discard, ui."""

from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from pathlib import Path

from dualai.actions import ActionError, approve_run, discard_run
from dualai.agents import AUTH_MODES, Agent, fake_author, fake_critic, run_claude, run_codex
from dualai.pipeline import RunConfig, run
from dualai.runlog import RunLog, runs_dir
from dualai.web.server import DEFAULT_PORT, serve
from dualai.workspace import WorkspaceError

# `subscription` usa o login salvo da CLI; a cobrança depende da conta autenticada.
AGENTS: dict[str, Agent] = {"claude": run_claude, "codex": run_codex}

EXIT_BY_STATUS = {"done": 0, "needs_decision": 2}


class CliError(Exception):
    pass


def _pick(name: str, args) -> Agent:
    if name not in AGENTS:
        available = ", ".join(sorted(AGENTS)) or "nenhum"
        raise CliError(f"agente não disponível: {name} (disponíveis: {available})")
    options = {"auth": getattr(args, f"{name}_auth", "subscription")}
    model = getattr(args, f"{name}_model", None)
    if model:
        options["model"] = model
    return partial(AGENTS[name], **options)


def _cmd_run(args) -> int:
    if args.fake:
        author, critic, author_name, critic_name = fake_author, fake_critic, "fake", "fake"
    else:
        author, author_name = _pick(args.author, args), args.author
        critic_name = args.critic
        critic = None if args.critic == "none" else _pick(args.critic, args)
    outcome = run(RunConfig(
        task=args.task, repo=Path(args.repo), test_cmd=args.test_cmd,
        author=author, critic=critic, author_name=author_name, critic_name=critic_name,
    ))
    summary = RunLog(Path(args.repo), outcome.run_id).dir / "summary.md"
    print(f"Execução: {outcome.run_id}")
    print(f"Estado: {outcome.status}" + (f" ({outcome.reason})" if outcome.reason else ""))
    print(f"Chamadas a modelo: {outcome.calls_used}")
    print(f"Resumo: {summary}")
    return EXIT_BY_STATUS.get(outcome.status, 1)


def _cmd_status(args) -> int:
    base = runs_dir(Path(args.repo))
    folders = sorted(path for path in base.iterdir() if path.is_dir()) if base.is_dir() else []
    if not folders:
        print("Nenhuma execução neste repositório.")
        return 0
    for folder in folders:
        state = RunLog(Path(args.repo), folder.name).read_json("state.json")
        decision = f"  [{state['decision']}]" if state.get("decision") else ""
        print(f"{folder.name}  {state.get('status', '?')}  chamadas={state.get('calls_used', 0)}{decision}")
    return 0


def _cmd_approve(args) -> int:
    approve_run(Path(args.repo), args.run_id)
    print(f"Aprovada: {args.run_id}. As alterações estão no seu branch.")
    return 0


def _cmd_discard(args) -> int:
    discard_run(Path(args.repo), args.run_id)
    print(f"Descartada: {args.run_id}. O seu branch não foi alterado.")
    return 0


def _cmd_ui(args) -> int:
    return serve(port=args.port, open_browser=not args.no_browser,
                 config_path=Path(args.config) if args.config else None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualai", description="Faz Claude Code e Codex CLI colaborarem em uma tarefa de código.")
    commands = parser.add_subparsers(dest="command", required=True)

    run_cmd = commands.add_parser("run", help="executa uma tarefa: autor, testes, revisão, resolução")
    run_cmd.add_argument("task", help="a tarefa, em texto livre")
    run_cmd.add_argument("--repo", default=".", help="raiz do repositório git alvo (padrão: pasta atual)")
    run_cmd.add_argument("--test-cmd", required=True, help="comando que verifica o resultado")
    run_cmd.add_argument("--author", default="claude", help="quem escreve (padrão: claude)")
    run_cmd.add_argument("--critic", default="codex", help='quem revisa, ou "none" (padrão: codex)')
    run_cmd.add_argument("--fake", action="store_true", help="usa agentes falsos; nenhuma chamada a modelo")
    for name in sorted(AGENTS):
        run_cmd.add_argument(
            f"--{name}-auth", choices=AUTH_MODES, default="subscription",
            help=f"como o {name} se autentica: subscription usa o login salvo da CLI; "
                 "a cobrança depende da conta. api_key usa chave de API em variável de ambiente")
        run_cmd.add_argument(
            f"--{name}-model", help=f"modelo do {name} nesta tarefa (padrão: escolha da própria CLI)")
    run_cmd.set_defaults(handler=_cmd_run)

    status_cmd = commands.add_parser("status", help="lista as execuções")
    status_cmd.add_argument("--repo", default=".")
    status_cmd.set_defaults(handler=_cmd_status)

    for name, handler, text in (("approve", _cmd_approve, "leva o resultado ao seu branch"),
                                ("discard", _cmd_discard, "descarta o resultado")):
        command = commands.add_parser(name, help=text)
        command.add_argument("run_id")
        command.add_argument("--repo", default=".")
        command.set_defaults(handler=handler)
    ui_cmd = commands.add_parser("ui", help="abre a interface no navegador (só neste computador)")
    ui_cmd.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"porta local (padrão: {DEFAULT_PORT})")
    ui_cmd.add_argument("--no-browser", action="store_true", help="não abre o navegador sozinho")
    ui_cmd.add_argument("--config", help="arquivo de configuração da interface (padrão: ~/.dualai/ui.json)")
    ui_cmd.set_defaults(handler=_cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return 0 if stop.code in (0, None) else 1
    if hasattr(args, "repo"):
        args.repo = os.path.abspath(args.repo)
    try:
        return args.handler(args)
    except (CliError, ActionError, WorkspaceError, ValueError) as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

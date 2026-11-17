# Amostra gravada em 2026-11-14 com codex-cli 0.167.1 (tests/fixtures/codex-exec.jsonl).
# O uso de tokens vem no evento {"type": "turn.completed", "usage": {...}}, campos
# "input_tokens" (inclui os de cache, "cached_input_tokens") e "output_tokens".
import subprocess
from pathlib import Path

from conftest import fake_spawn, raises, req

from dualai.agents import (
    build_codex_args,
    child_env,
    parse_codex_usage,
    resolve_codex_launcher,
    run_codex,
)
from dualai.findings import REVIEW_SCHEMA

FIXTURE = Path(__file__).parent / "fixtures" / "codex-exec.jsonl"
LAUNCHER = ["codex-de-teste"]


def test_read_request_uses_read_only_sandbox_and_stdin(tmp_path):
    args = build_codex_args(req("PROMPT-SECRETO", tmp_path), tmp_path / "s.json", tmp_path / "o.txt", None)
    assert args[:3] == ["--ask-for-approval", "never", "exec"]
    assert args[args.index("--sandbox") + 1] == "read-only" and args[-1] == "-"
    assert "PROMPT-SECRETO" not in args and "--json" in args
    assert args[args.index("--output-schema") + 1] == str(tmp_path / "s.json")


def test_write_request_is_confined_to_the_workspace(tmp_path):
    args = build_codex_args(req("p", tmp_path, access="write"), None, tmp_path / "o.txt", None)
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=false" in args
    assert "danger-full-access" not in " ".join(args) and "--output-schema" not in args


def test_child_env_drops_secrets():
    source = {"Path": "x", "OPENAI_API_KEY": "k", "CODEX_API_KEY": "c", "ANTHROPIC_API_KEY": "a",
              "CLAUDE_CODE_MESSAGING_TOKEN": "t"}
    assert child_env(source, "subscription") == {"Path": "x"}
    assert child_env(source, "api_key") == {"Path": "x", "CODEX_API_KEY": "c"}


def test_windows_launcher_resolution():
    env = {"PATH": r"C:\npm"}
    shim = lambda p: p in (r"C:\npm\codex.cmd", r"C:\npm\node_modules\@openai\codex\bin\codex.js")
    assert resolve_codex_launcher("win32", env, shim, "node.exe") == [
        "node.exe", r"C:\npm\node_modules\@openai\codex\bin\codex.js"]
    assert resolve_codex_launcher("win32", env, lambda p: p == r"C:\npm\codex.exe", "node.exe") == [r"C:\npm\codex.exe"]
    assert resolve_codex_launcher("linux", env, lambda p: False, "node") == ["codex"]


def test_usage_is_read_from_the_recorded_sample():
    tokens_in, tokens_out = parse_codex_usage(FIXTURE.read_text(encoding="utf-8"))
    assert tokens_in > 0 and tokens_out > 0


def test_prompt_goes_by_stdin_with_a_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "segredo")
    spawn = fake_spawn(0, last_message="ok")
    result = run_codex(req("PROMPT-SECRETO", tmp_path), spawn=spawn, launcher=LAUNCHER)
    assert result.status == "ok" and result.text == "ok"
    assert spawn.kwargs["input"] == "PROMPT-SECRETO" and "PROMPT-SECRETO" not in spawn.args
    assert "OPENAI_API_KEY" not in spawn.kwargs["env"] and spawn.kwargs["timeout"] == 5
    assert spawn.args[0] == "codex-de-teste"


def test_structured_answer_is_parsed(tmp_path):
    result = run_codex(req("p", tmp_path, schema=REVIEW_SCHEMA),
                       spawn=fake_spawn(0, last_message='{"findings": []}'), launcher=LAUNCHER)
    assert (result.status, result.data) == ("ok", {"findings": []})


def test_answer_that_is_not_json_leaves_data_empty_for_the_repair_rule(tmp_path):
    result = run_codex(req("p", tmp_path, schema=REVIEW_SCHEMA),
                       spawn=fake_spawn(0, last_message="não consegui"), launcher=LAUNCHER)
    assert (result.status, result.data, result.text) == ("ok", None, "não consegui")


def test_limit_timeout_and_error_are_classified(tmp_path):
    def status(spawn):
        return run_codex(req("p", tmp_path), spawn=spawn, launcher=LAUNCHER).status
    assert status(fake_spawn(1, stderr="You've hit your usage limit")) == "limit_reached"
    assert status(fake_spawn(1, stdout='{"type":"error","message":"Rate limit reached"}')) == "limit_reached"
    assert status(raises(subprocess.TimeoutExpired("codex", 5))) == "timeout"
    assert status(fake_spawn(1, stderr="boom")) == "error"
    assert status(fake_spawn(1, stdout='{"type":"turn.completed","usage":{"input_tokens":16429}}')) == "error"


def test_limit_words_in_a_successful_answer_do_not_count(tmp_path):
    ok = run_codex(req("p", tmp_path), spawn=fake_spawn(0, last_message="o código trata rate limit"),
                   launcher=LAUNCHER)
    assert ok.status == "ok"


def test_api_key_mode_without_a_key_stops_before_spawning(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    result = run_codex(req("p", tmp_path), auth="api_key", spawn=raises(AssertionError("não deveria rodar")),
                       launcher=LAUNCHER)
    assert result.status == "error" and "CODEX_API_KEY" in result.detail


def test_selected_model_reaches_the_codex_subprocess(tmp_path):
    spawn = fake_spawn(0, last_message="ok")
    result = run_codex(req("p", tmp_path), model="modelo-de-teste", spawn=spawn, launcher=LAUNCHER)
    assert result.status == "ok"
    assert spawn.args[spawn.args.index("-m") + 1] == "modelo-de-teste"

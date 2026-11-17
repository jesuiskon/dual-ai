# Amostra gravada em 2026-11-14 com Claude Code 2.1.319 em modo --restricted
# (tests/fixtures/claude-print.json), reduzida aos campos usados e sem caminhos locais.
# Os tokens vêm de "modelUsage" (inputTokens + cacheReadInputTokens +
# cacheCreationInputTokens, e outputTokens), somados entre os modelos.
import json
import subprocess
from pathlib import Path

import pytest

from conftest import fake_spawn, raises, req

from dualai.agents import CLAUDE_CLEAN_FLAGS, build_claude_args, child_env, parse_claude_output, run_claude
from dualai.findings import REVIEW_SCHEMA

FIXTURE = Path(__file__).parent / "fixtures" / "claude-print.json"
EXE = "claude-de-teste"


def test_read_request_has_no_write_tools_and_no_shell(tmp_path):
    args = build_claude_args(req("PROMPT-SECRETO", tmp_path), None, "subscription", [])
    assert args[args.index("--allowedTools") + 1] == "Read,Glob,Grep"
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep"
    assert args[args.index("--permission-mode") + 1] == "dontAsk" and "PROMPT-SECRETO" not in args


def test_write_request_adds_edit_and_write_but_never_bash(tmp_path):
    args = build_claude_args(req("p", tmp_path, access="write"), None, "subscription", [])
    assert args[args.index("--allowedTools") + 1] == "Read,Glob,Grep,Edit,Write"
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep,Edit,Write"
    assert "Bash" not in " ".join(args)


def test_subscription_mode_never_uses_bare(tmp_path):
    assert "--bare" not in build_claude_args(req("p", tmp_path), None, "subscription", [])
    api = build_claude_args(req("p", tmp_path), None, "api_key", [])
    assert "--bare" in api and api[api.index("--max-budget-usd") + 1] == "1.00"


def test_every_call_has_a_spending_cap(tmp_path):
    args = build_claude_args(req("p", tmp_path), None, "subscription", [])
    assert args[args.index("--max-budget-usd") + 1] == "1.00"


def test_schema_is_passed_as_json(tmp_path):
    args = build_claude_args(req("p", tmp_path, schema=REVIEW_SCHEMA), None, "subscription", [])
    assert json.loads(args[args.index("--json-schema") + 1]) == REVIEW_SCHEMA


def test_clean_flags_use_the_official_restricted_mode_and_disable_nothing():
    assert "--restricted" in CLAUDE_CLEAN_FLAGS
    assert not {"--bare", "--dangerously-skip-permissions"} & set(CLAUDE_CLEAN_FLAGS)
    assert "bypassPermissions" not in " ".join(CLAUDE_CLEAN_FLAGS)


def test_claude_child_env_drops_secrets_and_session_variables():
    source = {"Path": "x", "APPDATA": "a", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u",
              "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_SESSION_ID": "s", "OPENAI_API_KEY": "o"}
    assert child_env(source, "subscription", "claude") == {"Path": "x", "APPDATA": "a"}
    assert child_env(source, "api_key", "claude") == {"Path": "x", "APPDATA": "a", "ANTHROPIC_API_KEY": "k"}


def test_recorded_output_is_parsed():
    result = parse_claude_output(FIXTURE.read_text(encoding="utf-8"))
    assert result.status == "ok" and result.tokens_in > 0 and result.tokens_out > 0
    assert "ola.txt" in result.text


def test_structured_output_becomes_data():
    stdout = json.dumps({"is_error": False, "result": "", "structured_output": {"findings": []},
                         "usage": {"input_tokens": 5, "output_tokens": 1}})
    result = parse_claude_output(stdout)
    assert result.data == {"findings": []} and (result.tokens_in, result.tokens_out) == (5, 1)


def test_error_results_are_classified():
    def status(**fields):
        return parse_claude_output(json.dumps({"is_error": True, **fields})).status
    assert status(subtype="error_max_budget_usd", errors=["Reached maximum budget ($1)"]) == "error"
    assert status(result="Claude AI usage limit reached|1763164800") == "limit_reached"
    assert status(result="API Error: 429 rate_limit_error") == "limit_reached"
    assert status(result="boom", usage={"input_tokens": 16429}) == "error"


def test_run_sends_the_prompt_by_stdin_with_clean_flags_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://exemplo.invalid")
    spawn = fake_spawn(0, stdout=FIXTURE.read_text(encoding="utf-8"))
    result = run_claude(req("PROMPT-SECRETO", tmp_path, access="write"), spawn=spawn, executable=EXE)
    assert result.status == "ok"
    assert spawn.args[0] == EXE and "--restricted" in spawn.args and "PROMPT-SECRETO" not in spawn.args
    assert spawn.kwargs["input"] == "PROMPT-SECRETO" and spawn.kwargs["cwd"] == str(tmp_path)
    assert "ANTHROPIC_BASE_URL" not in spawn.kwargs["env"]


def test_json_answer_in_the_text_is_used_when_structured_output_is_missing(tmp_path):
    stdout = json.dumps({"is_error": False, "result": '```json\n{"findings": []}\n```'})
    result = run_claude(req("p", tmp_path, schema=REVIEW_SCHEMA), spawn=fake_spawn(0, stdout=stdout), executable=EXE)
    assert result.data == {"findings": []}


def test_limit_timeout_and_error_are_classified(tmp_path):
    def status(spawn):
        return run_claude(req("p", tmp_path), spawn=spawn, executable=EXE).status
    assert status(fake_spawn(1, stdout="rate_limit: 429")) == "limit_reached"
    assert status(raises(subprocess.TimeoutExpired("claude", 5))) == "timeout"
    assert status(fake_spawn(1, stdout="boom")) == "error"


def test_api_key_mode_without_a_key_stops_before_spawning(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = run_claude(req("p", tmp_path), auth="api_key", spawn=raises(AssertionError("não deveria rodar")),
                        executable=EXE)
    assert result.status == "error" and "ANTHROPIC_API_KEY" in result.detail


@pytest.mark.parametrize("output", [
    {},
    {"error": "falha de autenticação"},
    {"is_error": "false", "result": "ok"},
    {"type": "system", "is_error": False, "result": "ok"},
    {"is_error": False, "subtype": "error_during_execution", "result": "parcial"},
    {"is_error": False},
])
def test_unknown_or_inconsistent_json_envelopes_are_not_success(output):
    result = parse_claude_output(json.dumps(output))
    assert result.status == "error" and "envelope inválido" in result.detail


def test_nonzero_exit_overrides_a_successful_json_result_and_keeps_usage(tmp_path):
    spawn = fake_spawn(23, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="falha final sentinela")
    result = run_claude(req("p", tmp_path), spawn=spawn, executable=EXE)
    assert result.status == "error"
    assert "código 23" in result.detail and "falha final sentinela" in result.detail
    assert result.tokens_in > 0 and result.tokens_out > 0 and result.data is None


def test_nonzero_json_error_preserves_stderr_limit_classification(tmp_path):
    spawn = fake_spawn(1, stdout='{"error":"sem autenticação"}', stderr="API Error: 429 rate_limit_error")
    result = run_claude(req("p", tmp_path), spawn=spawn, executable=EXE)
    assert result.status == "limit_reached" and "429" in result.detail


def test_invalid_usage_fields_return_an_error_instead_of_raising():
    result = parse_claude_output(json.dumps({"is_error": False, "result": "ok",
                                            "usage": {"input_tokens": "inválido"}}))
    assert result.status == "error" and "métricas inválidas" in result.detail


def test_successful_answer_mentioning_limits_is_not_a_provider_error():
    result = parse_claude_output(json.dumps({"is_error": False, "result": "o código trata rate limit"}))
    assert result.status == "ok"


def test_selected_model_reaches_the_claude_subprocess(tmp_path):
    spawn = fake_spawn(0, stdout=FIXTURE.read_text(encoding="utf-8"))
    result = run_claude(req("p", tmp_path), model="modelo-de-teste", spawn=spawn, executable=EXE)
    assert result.status == "ok"
    assert spawn.args[spawn.args.index("--model") + 1] == "modelo-de-teste"

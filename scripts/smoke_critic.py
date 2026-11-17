"""Teste de fumaça do crítico real: UMA chamada ao Codex, em um repositório de brinquedo.

O autor é falso e planta dois defeitos conhecidos em textkit/paging.py. O
crítico é o Codex de verdade. O autor falso encaminha todo achado bloqueante,
então o estado esperado é needs_decision.

Com --resolver-claude, a resolução é feita pelo Claude de verdade (mais uma
chamada real): ele recebe os achados do Codex e corrige. Estado esperado: done.

Uso: uv run python scripts/smoke_critic.py <repo-de-brinquedo> [--resolver-claude]
"""

import re
import sys
from pathlib import Path

from dualai.agents import AgentResult, FakeAgent, run_claude, run_codex
from dualai.pipeline import RunConfig, run
from dualai.runlog import RunLog

TASK = (
    "Adicionar textkit/paging.py com paginate(items, page, page_size), com páginas numeradas a partir "
    "de 1, e page_count(total_items, page_size), que conta as páginas necessárias incluindo a última "
    "página parcial."
)

BUGGY = '''def paginate(items, page, page_size):
    """Return the items on `page`. Pages are numbered from 1."""
    start = page * page_size
    return items[start:start + page_size]


def page_count(total_items, page_size):
    """Return how many pages are needed to show `total_items`."""
    return total_items // page_size
'''


def plant(request):
    (request.cwd / "textkit" / "paging.py").write_text(BUGGY, encoding="utf-8")
    return AgentResult("ok", text="criei textkit/paging.py")


def escalate_all(request):
    ids = list(dict.fromkeys(re.findall(r"^\[(F\d+)\] \(", request.prompt, flags=re.MULTILINE)))
    return AgentResult("ok", data={"resolutions": [
        {"finding_id": finding_id, "state": "escalated", "note": "teste de fumaça"} for finding_id in ids]})


def main() -> int:
    repo = Path(sys.argv[1])
    real_resolver = "--resolver-claude" in sys.argv[2:]
    outcome = run(RunConfig(
        task=TASK, repo=repo, test_cmd="python -m unittest discover -s tests -t .",
        author=FakeAgent([plant, run_claude if real_resolver else escalate_all]), critic=run_codex,
        author_name="fake+claude" if real_resolver else "fake", critic_name="codex",
    ))
    log = RunLog(repo, outcome.run_id)
    print(f"execucao={outcome.run_id} estado={outcome.status} motivo={outcome.reason!r} chamadas={outcome.calls_used}")
    for finding in log.read_json("review.json").get("blocking", []):
        print(f"BLOQUEANTE [{finding['id']}] ({finding['severity']}) {finding['file']}:{finding['line']} {finding['title']}")
        print(f"    cenario: {finding['scenario']}")
    for finding in log.read_json("review.json").get("backlog", []):
        print(f"backlog [{finding['id']}] ({finding['severity']}) {finding['title']}")
    for resolution in log.read_json("resolution.json").get("resolutions", []):
        print(f"RESOLUCAO {resolution['finding_id']}: {resolution['state']} - {resolution['note']}")
    print(f"testes_passam={log.read_json('state.json').get('tests_pass')}")
    for call in log.read_json("metrics.json").get("calls", []):
        print(f"chamada {call['role']}/{call['agent']}: {call['status']} "
              f"tokens_in={call['tokens_in']} tokens_out={call['tokens_out']} segundos={call['seconds']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

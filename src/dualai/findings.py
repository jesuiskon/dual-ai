"""Achados do crítico: formato, triagem por severidade e regras de fechamento."""

from __future__ import annotations

from dataclasses import dataclass, replace

SEVERITIES = ("critico", "relevante", "opcional", "preferencia")
BLOCKING = ("critico", "relevante")
STATES = ("resolved", "justified", "escalated")

# Os dois schemas servem à saída estruturada estrita: todo objeto fecha as
# propriedades e exige todas elas.
REVIEW_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "title", "file", "line", "scenario", "suggestion"],
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "title": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "scenario": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
            },
        }
    },
}

RESOLUTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["resolutions"],
    "properties": {
        "resolutions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "state", "note"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "state": {"type": "string", "enum": list(STATES)},
                    "note": {"type": "string"},
                },
            },
        }
    },
}

CHECK_OUTPUT_TAIL = 6000


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    title: str
    file: str
    line: int | None
    scenario: str
    suggestion: str
    demoted_from: str | None = None


@dataclass(frozen=True)
class Resolution:
    finding_id: str
    state: str
    note: str


def _items(data, key: str) -> list:
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise ValueError(f'a resposta precisa ser um objeto com a lista "{key}"')
    return data[key]


def _text(item: dict, key: str) -> str:
    value = item.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f'"{key}" precisa ser texto')
    return value


def parse_review(data: dict) -> list[Finding]:
    findings = []
    for number, item in enumerate(_items(data, "findings"), 1):
        if not isinstance(item, dict):
            raise ValueError(f"achado {number}: precisa ser um objeto")
        severity = item.get("severity")
        if severity not in SEVERITIES:
            raise ValueError(f"achado {number}: severidade inválida: {severity!r}; use uma de {SEVERITIES}")
        title = _text(item, "title").strip()
        if not title:
            raise ValueError(f"achado {number}: falta o título")
        line = item.get("line")
        if isinstance(line, bool) or not isinstance(line, (int, type(None))):
            raise ValueError(f'achado {number}: "line" precisa ser um número inteiro ou null')
        findings.append(Finding(
            id=f"F{number}",
            severity=severity,
            title=title,
            file=_text(item, "file").strip(),
            line=line,
            scenario=_text(item, "scenario").strip(),
            suggestion=_text(item, "suggestion").strip(),
        ))
    return findings


def triage(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Separa (bloqueantes, backlog). Bloqueante sem arquivo, linha ou cenário é rebaixado."""
    blocking, backlog = [], []
    for finding in findings:
        if finding.severity not in BLOCKING:
            backlog.append(finding)
        elif finding.file and finding.line is not None and finding.scenario:
            blocking.append(finding)
        else:
            backlog.append(replace(finding, severity="opcional", demoted_from=finding.severity))
    return blocking, backlog


def tests_failed_finding(check_output: str) -> Finding:
    return Finding(
        id="F0",
        severity="critico",
        title="O comando de testes falhou",
        file="(comando de testes)",
        line=None,
        scenario="Saída do comando de testes:\n" + check_output[-CHECK_OUTPUT_TAIL:],
        suggestion="Corrigir o código até o comando de testes passar.",
    )


def parse_resolution(data: dict, blocking: list[Finding]) -> list[Resolution]:
    known = {finding.id for finding in blocking}
    by_id: dict[str, Resolution] = {}
    for item in _items(data, "resolutions"):
        if not isinstance(item, dict):
            raise ValueError("cada resolução precisa ser um objeto")
        finding_id, state = item.get("finding_id"), item.get("state")
        note = _text(item, "note").strip()
        if finding_id not in known:
            raise ValueError(f"resolução para achado desconhecido: {finding_id!r}; os ids são {sorted(known)}")
        if state not in STATES:
            raise ValueError(f"{finding_id}: estado inválido: {state!r}; use um de {STATES}")
        if state == "justified" and not note:
            raise ValueError(f'{finding_id}: "justified" exige o motivo em "note"')
        if finding_id in by_id:
            raise ValueError(f"{finding_id}: mais de uma resolução para o mesmo achado")
        by_id[finding_id] = Resolution(finding_id, state, note)
    missing = [finding.id for finding in blocking if finding.id not in by_id]
    if missing:
        raise ValueError(f"faltou resolução para: {missing}")
    return [by_id[finding.id] for finding in blocking]


def close_findings(blocking: list[Finding], resolutions: list[Resolution]) -> list[Resolution]:
    """Aplica a regra final: crítico apenas justificado sobe para decisão do usuário."""
    severity = {finding.id: finding.severity for finding in blocking}
    closed = []
    for resolution in resolutions:
        if resolution.state == "justified" and severity.get(resolution.finding_id) == "critico":
            resolution = replace(resolution, state="escalated")
        closed.append(resolution)
    return closed


def final_status(resolutions: list[Resolution], tests_pass: bool) -> str:
    if not tests_pass or any(resolution.state == "escalated" for resolution in resolutions):
        return "needs_decision"
    return "done"

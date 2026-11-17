from dataclasses import replace

import pytest

from dualai.findings import (
    Finding,
    Resolution,
    close_findings,
    final_status,
    parse_resolution,
    parse_review,
    triage,
)
from dualai.findings import tests_failed_finding as failed_tests_finding  # o nome original seria coletado pelo pytest


RAW = {"findings": [
    {"severity": "relevante", "title": "Offset errado", "file": "a.py", "line": 3,
     "scenario": "paginate([0,1],1,1) devolve [1]; esperado [0]", "suggestion": "usar page-1"},
    {"severity": "critico", "title": "Sem cenário", "file": "a.py", "line": 9, "scenario": "", "suggestion": ""},
    {"severity": "preferencia", "title": "Nome", "file": "", "line": None, "scenario": "", "suggestion": ""}]}


def test_parse_assigns_sequential_ids():
    assert [f.id for f in parse_review(RAW)] == ["F1", "F2", "F3"]


@pytest.mark.parametrize("bad", [{}, {"findings": "x"}, {"findings": [{"severity": "alto"}]}])
def test_parse_rejects_invalid_shapes(bad):
    with pytest.raises(ValueError):
        parse_review(bad)


def test_triage_demotes_blocking_finding_without_scenario():
    blocking, backlog = triage(parse_review(RAW))
    assert [f.id for f in blocking] == ["F1"]
    assert (backlog[0].id, backlog[0].severity, backlog[0].demoted_from) == ("F2", "opcional", "critico")
    assert [f.id for f in backlog] == ["F2", "F3"]


def test_triage_demotes_blocking_finding_without_file():
    f = replace(parse_review(RAW)[0], file="")
    assert triage([f])[0] == []


def test_triage_demotes_blocking_finding_without_line():
    f = replace(parse_review(RAW)[0], line=None)
    assert triage([f])[0] == []


def test_tests_failed_finding_is_critical_f0():
    f = failed_tests_finding("FAILED test_x")
    assert (f.id, f.severity) == ("F0", "critico") and "FAILED test_x" in f.scenario


def test_resolution_must_cover_every_blocking_finding():
    blocking, _ = triage(parse_review(RAW))
    with pytest.raises(ValueError):
        parse_resolution({"resolutions": []}, blocking)


def test_justified_needs_a_note():
    blocking, _ = triage(parse_review(RAW))
    with pytest.raises(ValueError):
        parse_resolution({"resolutions": [{"finding_id": "F1", "state": "justified", "note": " "}]}, blocking)


@pytest.mark.parametrize("item", [
    {"finding_id": "F9", "state": "resolved", "note": "x"},
    {"finding_id": "F1", "state": "ignorado", "note": "x"}])
def test_resolution_rejects_unknown_id_and_unknown_state(item):
    blocking, _ = triage(parse_review(RAW))
    with pytest.raises(ValueError):
        parse_resolution({"resolutions": [item]}, blocking)


def test_valid_resolution_is_returned_in_finding_order():
    blocking, _ = triage(parse_review(RAW))
    res = parse_resolution({"resolutions": [{"finding_id": "F1", "state": "resolved", "note": "feito"}]}, blocking)
    assert res == [Resolution("F1", "resolved", "feito")]


def test_justified_critical_becomes_escalated():
    crit = Finding("F1", "critico", "t", "a.py", 1, "cenario", "")
    assert close_findings([crit], [Resolution("F1", "justified", "motivo")])[0].state == "escalated"


def test_justified_relevant_stays_justified():
    rel = Finding("F1", "relevante", "t", "a.py", 1, "cenario", "")
    assert close_findings([rel], [Resolution("F1", "justified", "motivo")])[0].state == "justified"


@pytest.mark.parametrize("states,tests_pass,expected", [
    (["resolved", "justified"], True, "done"),
    ([], True, "done"),
    (["resolved", "escalated"], True, "needs_decision"),
    (["resolved"], False, "needs_decision")])
def test_final_status(states, tests_pass, expected):
    res = [Resolution(f"F{i}", s, "n") for i, s in enumerate(states, 1)]
    assert final_status(res, tests_pass) == expected

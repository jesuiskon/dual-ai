import pytest
from conftest import req

from dualai.agents import AgentResult, FakeAgent


def test_replays_script_and_records_requests(tmp_path):
    agent = FakeAgent([AgentResult("ok", text="um"), AgentResult("ok", text="dois")])
    first, second = agent(req("p1", tmp_path)), agent(req("p2", tmp_path))
    assert (first.text, second.text) == ("um", "dois")
    assert [r.prompt for r in agent.requests] == ["p1", "p2"]


def test_step_can_be_a_function_that_sees_the_request(tmp_path):
    agent = FakeAgent([lambda r: AgentResult("ok", text=r.access)])
    assert agent(req("p", tmp_path, access="write")).text == "write"


def test_fails_loudly_when_the_script_runs_out(tmp_path):
    with pytest.raises(AssertionError):
        FakeAgent([])(req("p", tmp_path))

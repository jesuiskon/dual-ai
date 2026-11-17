from datetime import datetime

from dualai.agents import AgentResult
from dualai.runlog import RunLog, load_all_metrics, new_run_id


def test_run_id_is_ascii_sortable_and_slugged():
    rid = new_run_id("Corrigir paginação: página 1!", datetime(2026, 11, 14, 15, 30, 12))
    assert rid == "20261114-153012-corrigir-paginacao-pagina-1"


def test_run_id_slug_is_capped_at_30_chars():
    assert len(new_run_id("x" * 80, datetime(2026, 1, 1)).split("-", 2)[2]) == 30


def test_run_folder_location(tmp_path):
    assert RunLog(tmp_path, "r1").dir == tmp_path / ".dualai" / "runs" / "r1"


def test_set_state_merges_fields(tmp_path):
    log = RunLog(tmp_path, "r1")
    log.set_state(status="running")
    log.set_state(calls_used=1)
    assert log.read_json("state.json") == {"status": "running", "calls_used": 1}


def test_text_is_written_as_utf8(tmp_path):
    log = RunLog(tmp_path, "r1")
    log.write_text("task.md", "ação\n\"x\"")
    assert (log.dir / "task.md").read_bytes() == "ação\n\"x\"".encode("utf-8")


def test_record_call_appends_to_metrics(tmp_path):
    log = RunLog(tmp_path, "r1")
    log.record_call("author", "fake", AgentResult("ok", tokens_in=10, tokens_out=2, seconds=1.5))
    assert log.read_json("metrics.json")["calls"] == [
        {"role": "author", "agent": "fake", "status": "ok", "tokens_in": 10, "tokens_out": 2, "seconds": 1.5}]


def test_load_all_metrics_reads_every_run(tmp_path):
    for rid in ("r1", "r2"):
        RunLog(tmp_path, rid).record_call("author", "fake", AgentResult("ok"))
    assert len(load_all_metrics(tmp_path)) == 2


def test_state_can_be_read_while_it_is_being_written(tmp_path):
    import threading

    log = RunLog(tmp_path, "r1")
    log.set_state(n=0)
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            log.set_state(n=n, padding="x" * 2000)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(2000):
            assert "n" in RunLog(tmp_path, "r1").read_json("state.json")
    finally:
        stop.set()
        thread.join()

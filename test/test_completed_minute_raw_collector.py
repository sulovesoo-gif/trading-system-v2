from datetime import datetime

from src.collector.runtime.completed_minute_raw_collector import CompletedMinuteRawCollector
from src.repository.raw_repository import RawWriteResult


class Sources:
    def __init__(self, codes): self.codes = codes
    def stock_codes(self): return self.codes


class Collector:
    def __init__(self, rows): self.rows = rows; self.calls = []
    def collect(self, **kwargs): self.calls.append(kwargs); return self.rows.get(kwargs["stock_code"], [])


class Repository:
    def __init__(self): self.rows = []
    def save(self, table, row): self.rows.append((table, row)); return RawWriteResult(table.value, 1, 1, 0)


def row(at):
    return {"bar_time": at}


def test_completed_minute_runtime_stores_only_immediately_previous_bar():
    now = datetime(2026, 8, 19, 10, 1, 8)
    collector = Collector({"005930": [row(datetime(2026, 8, 19, 10, 0))], "000660": [row(datetime(2026, 8, 19, 9, 59))]})
    repository = Repository()
    result = CompletedMinuteRawCollector(collector=collector, repository=repository, source_registry=Sources(("005930", "000660"))).run_cycle(now=now)
    assert result["005930"].inserted_count == 1
    assert result["000660"] == "NO_COMPLETED_BAR"
    assert len(repository.rows) == 1


def test_candidate_zero_sources_are_the_two_frozen_signal_sources():
    assert Sources(("005930", "000660")).stock_codes() == ("005930", "000660")

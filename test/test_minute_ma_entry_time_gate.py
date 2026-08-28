from datetime import date, datetime, time, timedelta
import unittest

from src.minute_ma.contracts import Axis, MinuteBar, MinuteMaPath
from src.minute_ma.engine import PreparedMaPoint, SignalEvent, SignalType
from src.minute_ma.runtime import MinuteMaPaperRuntime


class _OneEntryEngine:
    def __init__(self, source_time: datetime) -> None:
        self.source_time = source_time

    def prepare(self, *, path, bars):
        return (PreparedMaPoint(self.source_time, {3: 2.0, 5: 1.0}, {3: 1.0, 5: 2.0}),)

    def evaluate_prepared(self, *, path, points):
        return (SignalEvent(
            path.minute_path_id, path.path_key, SignalType.ENTRY,
            self.source_time, self.source_time + timedelta(minutes=1, seconds=1),
            f"event-{path.axis.value}-{self.source_time.time()}", True,
            {3: 2.0, 5: 1.0}, {3: 1.0, 5: 2.0},
        ),)


class _Repository:
    def __init__(self, axis: Axis, source_time: datetime) -> None:
        self.path = MinuteMaPath(1, "P", axis, "000660", "0193T0", "LONG", 3, 5, 3, 5, None)
        self.source_time = source_time
        self.created = 0

    def paths(self, axis): return (self.path,)
    def source_bars(self, **kwargs): return ()
    def runtime_cursor(self, **kwargs): return None
    def execution_watermark(self, **kwargs): return datetime.combine(self.source_time.date(), time(15, 19))
    def execution_bar(self, **kwargs):
        at = kwargs["at"]
        return MinuteBar(at, 100, 100, 100, 100)
    def apply_event(self, **kwargs):
        self.created += 1
        return 1, 0
    def record_non_executable(self, *args, **kwargs): return True
    def close_eod(self, **kwargs): return 0
    def advance_cursor(self, **kwargs): return None


class MinuteMaEntryTimeGateTest(unittest.TestCase):
    CASES = (
        (Axis.KRX_CONTINUOUS, "09:00", 1),
        (Axis.KRX_CONTINUOUS, "15:17", 1),
        (Axis.KRX_CONTINUOUS, "15:18", 1),
        (Axis.KRX_CONTINUOUS, "15:19", 0),
        (Axis.INTEGRATED_CONTINUOUS, "08:58", 0),
        (Axis.INTEGRATED_CONTINUOUS, "08:59", 0),
        (Axis.INTEGRATED_CONTINUOUS, "09:00", 1),
        (Axis.INTEGRATED_CONTINUOUS, "15:18", 1),
        (Axis.INTEGRATED_CONTINUOUS, "15:19", 0),
        (Axis.KRX_RESET, "09:00", 1),
        (Axis.KRX_RESET, "14:59", 1),
        (Axis.KRX_RESET, "15:00", 0),
        (Axis.KRX_RESET, "15:18", 0),
        (Axis.INTEGRATED_RESET, "08:59", 0),
        (Axis.INTEGRATED_RESET, "09:00", 1),
        (Axis.INTEGRATED_RESET, "14:59", 1),
        (Axis.INTEGRATED_RESET, "15:00", 0),
        (Axis.INTEGRATED_RESET, "19:30", 0),
        (Axis.KRX_CONTINUOUS_AFTERNOON, "13:59", 0),
        (Axis.KRX_CONTINUOUS_AFTERNOON, "14:00", 1),
        (Axis.KRX_CONTINUOUS_AFTERNOON, "15:18", 1),
        (Axis.KRX_CONTINUOUS_AFTERNOON, "15:19", 0),
        (Axis.KRX_RESET_AFTERNOON, "13:59", 0),
        (Axis.KRX_RESET_AFTERNOON, "14:00", 1),
        (Axis.KRX_RESET_AFTERNOON, "14:59", 1),
        (Axis.KRX_RESET_AFTERNOON, "15:00", 0),
        (Axis.INTEGRATED_CONTINUOUS_AFTERNOON, "08:59", 0),
        (Axis.INTEGRATED_CONTINUOUS_AFTERNOON, "13:59", 0),
        (Axis.INTEGRATED_CONTINUOUS_AFTERNOON, "14:00", 1),
        (Axis.INTEGRATED_CONTINUOUS_AFTERNOON, "15:18", 1),
        (Axis.INTEGRATED_CONTINUOUS_AFTERNOON, "15:19", 0),
        (Axis.INTEGRATED_CONTINUOUS_AFTERNOON, "19:30", 0),
        (Axis.INTEGRATED_RESET_AFTERNOON, "08:59", 0),
        (Axis.INTEGRATED_RESET_AFTERNOON, "13:59", 0),
        (Axis.INTEGRATED_RESET_AFTERNOON, "14:00", 1),
        (Axis.INTEGRATED_RESET_AFTERNOON, "14:59", 1),
        (Axis.INTEGRATED_RESET_AFTERNOON, "15:00", 0),
        (Axis.INTEGRATED_RESET_AFTERNOON, "19:30", 0),
    )

    def test_source_bar_entry_boundaries(self):
        trading_date = date(2026, 8, 26)
        for axis, clock, expected in self.CASES:
            with self.subTest(axis=axis.value, clock=clock):
                source_time = datetime.combine(trading_date, time.fromisoformat(clock))
                repository = _Repository(axis, source_time)
                result = MinuteMaPaperRuntime(
                    repository, engine=_OneEntryEngine(source_time)
                ).run_day(trading_date=trading_date, axis=axis)
                self.assertEqual(result.trades_created, expected)
                self.assertEqual(repository.created, expected)


if __name__ == "__main__":
    unittest.main()

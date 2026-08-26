from datetime import datetime,timedelta
import unittest

from src.minute_ma.contracts import Axis,MinuteBar,MinuteMaPath
from src.minute_ma.engine import MinuteMaSignalEngine,SignalType


def path(axis=Axis.KRX_CONTINUOUS,direction="LONG",trend=None):
    return MinuteMaPath(1,"P",axis,"000660","0193T0",direction,3,5,3,5,trend)


def bars(values,start=datetime(2026,8,26,9,0)):
    return tuple(MinuteBar(start+timedelta(minutes=i),v,v,v,v) for i,v in enumerate(values))


class MinuteMaEngineTest(unittest.TestCase):
    def test_held_condition_emits_one_transition_only(self):
        events=MinuteMaSignalEngine().evaluate(path=path(),bars=bars([5,4,3,2,1,2,3,4,5,6]))
        self.assertEqual([e.signal_type for e in events].count(SignalType.ENTRY),1)

    def test_long_down_cross_is_exit(self):
        events=MinuteMaSignalEngine().evaluate(path=path(),bars=bars([1,2,3,4,5,4,3,2,1]))
        self.assertEqual([e.signal_type for e in events].count(SignalType.EXIT),1)

    def test_replay_identity_is_deterministic(self):
        engine=MinuteMaSignalEngine();source=bars([5,4,3,2,1,2,3,4,5])
        first=engine.evaluate(path=path(),bars=source)
        second=engine.evaluate(path=path(),bars=source)
        self.assertEqual([e.signal_event_key for e in first],[e.signal_event_key for e in second])

    def test_reset_does_not_carry_previous_day_history(self):
        source=bars([3,2,1],datetime(2026,8,25,15,17))+bars([2,3],datetime(2026,8,26,9,0))
        reset=MinuteMaSignalEngine().evaluate(path=path(Axis.KRX_RESET),bars=source)
        self.assertEqual(reset,())

    def test_afternoon_variant_keeps_pre_1400_ma_history(self):
        source=bars([5,4,3,2,1,2,3,4],datetime(2026,8,26,13,54))
        afternoon=path(Axis.KRX_CONTINUOUS_AFTERNOON)
        prepared=MinuteMaSignalEngine().prepare(path=afternoon,bars=source)
        self.assertTrue(any(point.bar_time.time().isoformat() < "14:00:00" for point in prepared))
        self.assertEqual(
            [event.source_bar_time for event in MinuteMaSignalEngine().evaluate(
                path=afternoon,bars=source
            )],
            [event.source_bar_time for event in MinuteMaSignalEngine().evaluate(
                path=path(Axis.KRX_CONTINUOUS),bars=source
            )],
        )


if __name__=="__main__": unittest.main()

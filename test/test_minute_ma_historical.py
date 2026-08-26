from datetime import date,datetime,timedelta
from decimal import Decimal
import unittest

from src.minute_ma.contracts import Axis,MinuteBar,MinuteMaPath
from src.minute_ma.engine import SignalEvent,SignalType
from src.minute_ma.historical import MinuteMaHistoricalReplay,result_row


class _Events:
    def __init__(self,events):self.events=events
    def evaluate_prepared(self,**kwargs):return self.events


def _event(path,kind,clock,key):
    source=datetime.fromisoformat(f"2026-08-26T{clock}:00")
    return SignalEvent(path.minute_path_id,path.path_key,kind,source,
                       source+timedelta(minutes=1,seconds=1),key,True,{},{})


class MinuteMaHistoricalReplayTest(unittest.TestCase):
    def test_afternoon_filters_pre_1400_entry_and_uses_existing_eod_contract(self):
        path=MinuteMaPath(1,"P",Axis.KRX_CONTINUOUS_AFTERNOON,
                          "000660","0193T0","LONG",3,5,3,5,None,"DS1")
        events=(_event(path,SignalType.ENTRY,"13:59","early"),
                _event(path,SignalType.ENTRY,"14:00","allowed"))
        execution={
            datetime(2026,8,26,14,0):MinuteBar(datetime(2026,8,26,14,0),90,90,90,90),
            datetime(2026,8,26,14,1):MinuteBar(datetime(2026,8,26,14,1),100,100,100,100),
            datetime(2026,8,26,15,19):MinuteBar(datetime(2026,8,26,15,19),110,110,110,110),
        }
        replay=MinuteMaHistoricalReplay(engine=_Events(events))
        result=replay.replay(source_daily_strategy_id="DS1",path=path,prepared_points=(),
                             execution_bars=execution,evaluation_from=date(2026,8,26),
                             evaluation_to=date(2026,8,26))
        self.assertEqual(len(result.trades),1)
        self.assertEqual(result.trades[0].entry_execution_time,datetime(2026,8,26,14,1))
        self.assertEqual(result.trades[0].exit_reason,"EOD_1519")
        self.assertEqual(result.trades[0].net_return_pct,Decimal("9.80"))
        self.assertEqual(result.final_compound_capital,Decimal("1098000.00"))

    def test_replay_and_result_row_are_deterministic(self):
        path=MinuteMaPath(1,"P",Axis.KRX_RESET_AFTERNOON,
                          "000660","0193T0","LONG",3,5,3,5,None,"DS1")
        events=(_event(path,SignalType.ENTRY,"14:00","entry"),
                _event(path,SignalType.EXIT,"14:01","exit"))
        execution={
            datetime(2026,8,26,14,1):MinuteBar(datetime(2026,8,26,14,1),100,100,100,100),
            datetime(2026,8,26,14,2):MinuteBar(datetime(2026,8,26,14,2),101,101,101,101),
            datetime(2026,8,26,15,19):MinuteBar(datetime(2026,8,26,15,19),101,101,101,101),
        }
        replay=MinuteMaHistoricalReplay(engine=_Events(events))
        args=dict(source_daily_strategy_id="DS1",path=path,prepared_points=(),
                  execution_bars=execution,evaluation_from=date(2026,8,26),
                  evaluation_to=date(2026,8,26))
        self.assertEqual(result_row(replay.replay(**args)),result_row(replay.replay(**args)))


if __name__=="__main__":unittest.main()

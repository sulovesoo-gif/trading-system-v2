"""Read-only Golden intent planning verification; no broker send or persistence write."""
from __future__ import annotations
import sys
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.golden.validate_core_v1_1 import definition,raw_rows
from src.live_intent import InMemoryLiveIntentStore,LiveStrategyAdapter,LiveStrategyInstance,MarketContext
from src.live_order import AccountPolicy,InMemoryOrderPlanningStore,LiveOrderSafetyGate,OrderPlanner
from src.strategy_core import HistoricalDataProvider
def main():
 grouped=defaultdict(list)
 for stock,bar in raw_rows():grouped[stock].append(bar)
 provider=HistoricalDataProvider(grouped)
 instances=(LiveStrategyInstance('S1',definition(instance='SAMSUNG_S1_LONG_PULLBACK_WITHIN30_EOD',code='S1_OR_PULLBACK_RESTART',signal_stock='005930',signal_direction='LONG',execution_stock='0193W0').definition),LiveStrategyInstance('S2',definition(instance='SAMSUNG_S2_SHORT_FIXED30',code='S2_FAILED_OR_VWAP',signal_stock='005930',signal_direction='SHORT',execution_stock='0193L0').definition),LiveStrategyInstance('S3_3',definition(instance='HYNIX_S3_SHORT_3BAR',code='S3_VOLUME_CLIMAX_REVERSAL',signal_stock='000660',signal_direction='SHORT',execution_stock='0197X0').definition,'S3'),LiveStrategyInstance('S3_5',definition(instance='HYNIX_S3_SHORT_5BAR',code='S3_VOLUME_CLIMAX_REVERSAL',signal_stock='000660',signal_direction='SHORT',execution_stock='0197X0').definition,'S3'))
 intents=InMemoryLiveIntentStore();adapter=LiveStrategyAdapter(provider=provider,instances=instances,store=intents)
 for d in sorted({b.time.date().isoformat() for b in grouped['005930']}|{b.time.date().isoformat() for b in grouped['000660']}):adapter.process_completed_day(d,context=MarketContext())
 entries=[x for x in intents.intents.values() if x.intent_type.value=='ENTRY_INTENT'];planned=[]
 for item in entries:
  store=InMemoryOrderPlanningStore();store.ensure_account(item.strategy_instance_id,1_000_000)
  planner=OrderPlanner(store=store,gate=LiveOrderSafetyGate(),policy=AccountPolicy(40_000_000,30_000_000,4_000_000),whitelist={'0193W0','0193L0','0197X0'})
  ref=provider.bar_at(item.execution_stock_code,item.execution_target_time).open
  request,safety=planner.plan(intent=item,reference_price=ref,now=item.execution_target_time,live_enabled=True,global_trade_yn='N')
  if request:planned.append(request)
 result={'entry_intents':len(entries),'planned':len(planned),'by_instance':dict(Counter(x.strategy_instance_id for x in planned)),'broker_send_eligible':sum(bool(x.detail['broker_send_eligible']) for x in planned)}
 print(result)
 if result['planned']!=40 or result['broker_send_eligible']!=0:raise SystemExit(1)
if __name__=='__main__':main()

"""Durable Forward performance snapshot persistence; no broker dependency."""
from __future__ import annotations
from .contracts import ForwardPerformance
class PostgresForwardPerformanceStore:
 def __init__(self,connection_factory):self._connection_factory=connection_factory
 def save(self,path_id,performance:ForwardPerformance,**metrics):
  with self._connection_factory() as c,c.cursor() as q:
   q.execute("""INSERT INTO forward_performance_snapshot(forward_execution_id,actual_1share_pnl,cost_adjusted_actual_pnl,cumulative_simple_return,normalized_strategy_return,compound_equity,mdd,trade_count,win_rate,profit_factor,consecutive_losses,trade_return_pct,avg_win,avg_loss,mfe,mae,fill_rate,miss_rate,slippage,expected_vs_fill)
   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
   ON CONFLICT(forward_execution_id) DO UPDATE SET actual_1share_pnl=EXCLUDED.actual_1share_pnl,cost_adjusted_actual_pnl=EXCLUDED.cost_adjusted_actual_pnl,cumulative_simple_return=EXCLUDED.cumulative_simple_return,normalized_strategy_return=EXCLUDED.normalized_strategy_return,compound_equity=EXCLUDED.compound_equity,mdd=EXCLUDED.mdd,trade_count=EXCLUDED.trade_count,win_rate=EXCLUDED.win_rate,profit_factor=EXCLUDED.profit_factor,consecutive_losses=EXCLUDED.consecutive_losses,updated_at=CURRENT_TIMESTAMP""",(path_id,performance.actual_1share_pnl,performance.cost_adjusted_actual_pnl,performance.cumulative_simple_return,performance.normalized_strategy_return,performance.compound_equity,performance.mdd,performance.trades,performance.win_rate,performance.profit_factor,performance.consecutive_losses,metrics.get('trade_return_pct',0),metrics.get('avg_win',0),metrics.get('avg_loss',0),metrics.get('mfe',0),metrics.get('mae',0),metrics.get('fill_rate',0),metrics.get('miss_rate',0),metrics.get('slippage',0),metrics.get('expected_vs_fill',0)))
   c.commit()

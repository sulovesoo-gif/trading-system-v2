"""Durable 3-strike LIVE entry gate driven only by completed PAPER trades."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL,uuid5
@dataclass(frozen=True)
class RiskState: status:str='ENABLED'; streak:int=0
def next_risk_state(state:RiskState,return_pct:Decimal)->RiskState:
 if return_pct>0:return RiskState('ENABLED',0)
 if return_pct==0:return state
 streak=state.streak+1
 return RiskState('THREE_STRIKE_SUSPENDED' if streak>=3 else state.status,streak)
class PostgresThreeStrikeRiskStore:
 def __init__(self,connection_factory,*,commit=True):self._connection_factory,self._commit=connection_factory,commit
 def apply_completed_paper_trade(self,*,paper_trade_id:int,strategy_id:str,return_pct:Decimal):
  with self._connection_factory() as c,c.cursor() as q:
   q.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(f'daily-ma-three-strike|{strategy_id}',))
   q.execute("SELECT live_risk_status,consecutive_loss_streak FROM daily_strategy_live_risk_state WHERE strategy_id=%s FOR UPDATE",(strategy_id,));row=q.fetchone()
   if row is None:raise ValueError('LIVE_RISK_STATE_REQUIRED')
   q.execute("SELECT 1 FROM daily_strategy_live_risk_event WHERE paper_trade_id=%s",(paper_trade_id,))
   if q.fetchone() is not None:return RiskState(str(row[0]),int(row[1])),False
   prior=RiskState(str(row[0]),int(row[1]));new=next_risk_state(prior,Decimal(return_pct))
   reason='PAPER_WIN_RESET' if return_pct>0 else ('PAPER_LOSS_STRIKE' if return_pct<0 else 'PAPER_FLAT_HOLD')
   q.execute("""INSERT INTO daily_strategy_live_risk_event(risk_event_id,paper_trade_id,strategy_id,return_pct,prior_streak,resulting_streak,resulting_status,reason)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(str(uuid5(NAMESPACE_URL,f'daily-ma-risk|{paper_trade_id}')),paper_trade_id,strategy_id,return_pct,prior.streak,new.streak,new.status,reason))
   q.execute("UPDATE daily_strategy_live_risk_state SET live_risk_status=%s,consecutive_loss_streak=%s,last_risk_event_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE strategy_id=%s",(new.status,new.streak,strategy_id))
   if self._commit:c.commit()
  return new,True

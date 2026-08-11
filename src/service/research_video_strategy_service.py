"""Independent RAW-only runner for VIDEO_STRATEGY V1."""
from __future__ import annotations

from datetime import date
from uuid import UUID,uuid4

from src.analysis.video_strategy import VideoFeatureEngine,VideoParameters,measure_event


class ResearchVideoStrategyService:
    def __init__(self,repository): self.repository=repository

    def run(self,*,start_date:date,end_date:date,parameters:VideoParameters|None=None,run_id:UUID|None=None):
        p=parameters or VideoParameters(); run_id=run_id or uuid4(); snapshot=p.snapshot()
        self.repository.create_run(run_id,start_date,end_date,snapshot)
        try:
            source_rows=self.repository.minute_rows(p.signal_source_stock_code,start_date,end_date)
            features,events=VideoFeatureEngine(p).build(source_rows)
            self.repository.save_features(run_id,p.signal_source_stock_code,features)
            saved=self.repository.save_events(run_id,p.signal_source_stock_code,events)
            target_rows={code:self.repository.minute_rows(code,start_date,end_date) for code in p.execution_stock_codes}
            target_maps={code:{bar.bar_time:bar for bar,_volume in rows} for code,rows in target_rows.items()}
            for event_id,event in saved:
                for code in p.execution_stock_codes:
                    measurement=measure_event(event,target_maps[code],code)
                    self.repository.save_event_performance(event_id,code,event,measurement)
            # Cycles: exact timestamp ENTRY to first later EXIT/STOP; no price substitution.
            entries=[e for e in events if e.event_type in {"LONG_ENTRY","SHORT_ENTRY"}]
            exits=[e for e in events if e.event_type in {"EXIT","STOP","VOLUME_DIVERGENCE_EXIT","STRUCTURE_EXIT","REVERSAL_EXIT","BATTLE_WARNING_EXIT","SMA_EXIT"}]
            for entry in entries:
                exit_=next((e for e in exits if e.at>entry.at and e.direction==entry.direction),None)
                if exit_ is None: continue
                for code in p.execution_stock_codes:
                    m=measure_event(entry,target_maps[code],code); exit_bar=target_maps[code].get(exit_.at)
                    m["exit_price"]=None if exit_bar is None else exit_bar.close_price
                    self.repository.save_cycle(run_id,p.signal_source_stock_code,code,entry,exit_,m,snapshot)
            self.repository.save_daily_performance(run_id)
            self.repository.finish_run(run_id,"COMPLETED")
            return run_id
        except Exception:
            self.repository.finish_run(run_id,"FAILED"); raise

"""Run one VIDEO_STRATEGY V1 replay against stored RAW on an explicit test DB."""
from __future__ import annotations
import argparse,os,sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.analysis.video_strategy import VideoParameters
from src.repository.database import DatabaseSettings,create_connection_pool
from src.repository.research_video_strategy_repository import ResearchVideoStrategyRepository
from src.service.research_video_strategy_service import ResearchVideoStrategyService

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--start-date",required=True); parser.add_argument("--end-date",required=True)
    parser.add_argument("--pivot-method",default="PIVOT_FRACTAL_2"); parser.add_argument("--body-above-ratio",default="0.50")
    parser.add_argument("--volume-spike-ratio",default="2.0"); parser.add_argument("--ablation",default="FULL"); parser.add_argument("--variant",default="VIDEO_BASE")
    args=parser.parse_args(); load_dotenv(ROOT/".env")
    if "test" not in os.getenv("DB_NAME","").lower(): raise RuntimeError("VIDEO_STRATEGY replay is restricted to a test database")
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
        p=VideoParameters(pivot_method=args.pivot_method,body_above_ratio=Decimal(args.body_above_ratio),volume_spike_ratio=Decimal(args.volume_spike_ratio),ablation=args.ablation,variant=args.variant)
        repo=ResearchVideoStrategyRepository(pool); run_id=ResearchVideoStrategyService(repo).run(start_date=date.fromisoformat(args.start_date),end_date=date.fromisoformat(args.end_date),parameters=p)
        feature_count,event_count,cycle_count,missing=repo.summary(run_id)
        print(f"run_id={run_id} features={feature_count} events={event_count} cycles={cycle_count} trade_price_missing={missing}")
        return 0
    finally: pool.close()
if __name__=="__main__":raise SystemExit(main())

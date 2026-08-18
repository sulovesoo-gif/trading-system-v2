"""Semantics-complete Research execution-path identity and deduplication."""
from __future__ import annotations
import json
from dataclasses import dataclass
from hashlib import sha256
from collections.abc import Iterable
from src.strategy_core.registry import StrategyDefinition

def _json(value) -> str: return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def _digest(value) -> str: return sha256(_json(value).encode()).hexdigest()[:24]


def _is_frozen_live_equivalent(definition: StrategyDefinition) -> bool:
    """Compare executable semantics, never a strategy name or numeric id.

    The historical Research engine is deliberately distinct from the FROZEN
    LIVE S1 and S3 engines (S1 has WITHIN30 semantics and S3 has the live
    structure/stop policy).  The S2 30-minute failed-OR/VWAP path is the one
    shared executable semantic currently represented by the master matrix.
    """
    entry = dict(definition.entry_params)
    exit_params = dict(definition.exit_params)
    return (
        entry.get("strategy_group") == "S2_FAILED_OR_VWAP"
        and definition.signal_stock_code == "005930"
        and definition.signal_direction == "SHORT"
        and definition.execution_stock_code == "0193L0"
        and definition.execution_direction == "LONG"
        and entry.get("or_minutes") == 30
        and definition.exit_variant == "FIXED_30"
        and exit_params.get("hold_minutes") == 30
    )

@dataclass(frozen=True)
class ResearchExecutionPath:
 strategy_ids: tuple[int,...]; entry_identity: str; exit_identity: str; execution_stock_code: str; signal_stock_code: str; live_equivalent: bool=False
 @property
 def strategy_reference(self) -> str: return 'RESEARCH_PATH_'+_digest({'entry':self.entry_identity,'exit':self.exit_identity,'execution':self.execution_stock_code})

def path_for(definition: StrategyDefinition) -> ResearchExecutionPath:
 # All result-affecting parameters are included; display names never decide dedup.
 entry={'engine':'HISTORICAL_MASTER_V1','family':definition.entry_params.get('strategy_group'),'signal_stock_code':definition.signal_stock_code,'signal_direction':definition.signal_direction,'execution_direction':definition.execution_direction,'params':dict(definition.entry_params)}
 exit={'engine':'HISTORICAL_MASTER_V1','variant':definition.exit_variant,'params':dict(definition.exit_params)}
 return ResearchExecutionPath((int(definition.strategy_id),), 'ENTRY_'+_digest(entry), 'EXIT_'+_digest(exit), definition.execution_stock_code, definition.signal_stock_code, _is_frozen_live_equivalent(definition))

def deduplicate(definitions: Iterable[StrategyDefinition]) -> tuple[ResearchExecutionPath,...]:
 definitions=tuple(definitions)
 groups={}
 for definition in definitions:
  path=path_for(definition); key=(path.entry_identity,path.exit_identity,path.execution_stock_code)
  groups.setdefault(key,[]).append(int(definition.strategy_id))
 return tuple(
  ResearchExecutionPath(
   tuple(sorted(ids)), key[0], key[1], key[2],
   next(d.signal_stock_code for d in definitions if int(d.strategy_id)==ids[0]),
   any(path_for(d).live_equivalent for d in definitions if int(d.strategy_id) in ids),
  )
  for key,ids in sorted(groups.items())
 )

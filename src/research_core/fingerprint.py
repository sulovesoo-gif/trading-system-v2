"""Semantics-complete Research execution-path identity and deduplication."""
from __future__ import annotations
import json
from dataclasses import dataclass
from hashlib import sha256
from collections.abc import Iterable
from src.strategy_core.registry import StrategyDefinition

def _json(value) -> str: return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def _digest(value) -> str: return sha256(_json(value).encode()).hexdigest()[:24]

@dataclass(frozen=True)
class ResearchExecutionPath:
 strategy_ids: tuple[int,...]; entry_identity: str; exit_identity: str; execution_stock_code: str; signal_stock_code: str; live_equivalent: bool=False
 @property
 def strategy_reference(self) -> str: return 'RESEARCH_PATH_'+_digest({'entry':self.entry_identity,'exit':self.exit_identity,'execution':self.execution_stock_code})

def path_for(definition: StrategyDefinition) -> ResearchExecutionPath:
 # All result-affecting parameters are included; display names never decide dedup.
 entry={'engine':'HISTORICAL_MASTER_V1','family':definition.entry_params.get('strategy_group'),'signal_stock_code':definition.signal_stock_code,'signal_direction':definition.signal_direction,'execution_direction':definition.execution_direction,'params':dict(definition.entry_params)}
 exit={'engine':'HISTORICAL_MASTER_V1','variant':definition.exit_variant,'params':dict(definition.exit_params)}
 return ResearchExecutionPath((int(definition.strategy_id),), 'ENTRY_'+_digest(entry), 'EXIT_'+_digest(exit), definition.execution_stock_code, definition.signal_stock_code, False)

def deduplicate(definitions: Iterable[StrategyDefinition]) -> tuple[ResearchExecutionPath,...]:
 definitions=tuple(definitions)
 groups={}
 for definition in definitions:
  path=path_for(definition); key=(path.entry_identity,path.exit_identity,path.execution_stock_code)
  groups.setdefault(key,[]).append(int(definition.strategy_id))
 return tuple(ResearchExecutionPath(tuple(sorted(ids)),key[0],key[1],key[2], next(d.signal_stock_code for d in definitions if int(d.strategy_id)==ids[0])) for key,ids in sorted(groups.items()))

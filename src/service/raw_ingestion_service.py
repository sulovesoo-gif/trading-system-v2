"""Explicitly connect a Collector result to the RAW Repository."""

from __future__ import annotations

from src.repository.raw_repository import RawWriteResult
from src.repository.raw_specs import RawTable


class RawIngestionService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def store(self, table: RawTable, collector_result) -> RawWriteResult:
        return self.repository.save(table, collector_result)

"""Read-only broker-net versus durable logical-ownership reconciliation."""

from __future__ import annotations

from .ownership import ReconciliationResult


class ExecutionReconciliationService:
    def __init__(self, *, ownership_store, broker_lookup) -> None:
        self.ownership_store, self.broker_lookup = ownership_store, broker_lookup

    def reconcile(self):
        broker = self.broker_lookup.net_quantities()
        # PostgresOwnershipStore records an append-only audit in the same
        # transaction; in-memory test stores simply return the result.
        return self.ownership_store.reconcile(broker)

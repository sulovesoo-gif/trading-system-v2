-- Bind every 7C approval to its exact executable order scope.
-- This is additive: DDL 33 remains the base approval persistence contract.
BEGIN;

ALTER TABLE live_smoke_approval
    ADD COLUMN side VARCHAR(4),
    ADD COLUMN quantity INTEGER;

-- Applied only after the preflight confirms there are no approval rows.
ALTER TABLE live_smoke_approval
    ALTER COLUMN side SET NOT NULL,
    ALTER COLUMN quantity SET NOT NULL,
    ADD CONSTRAINT live_smoke_approval_side_check CHECK (side IN ('BUY', 'SELL')),
    ADD CONSTRAINT live_smoke_approval_quantity_check CHECK (quantity = 1);

COMMIT;

-- Bind every 7C-1 approval to the official KIS cash-order execution contract.
-- Additive only. Apply after confirming live_smoke_approval is empty.
BEGIN;

ALTER TABLE live_smoke_approval
    ADD COLUMN exchange VARCHAR(3),
    ADD COLUMN order_division VARCHAR(2),
    ADD COLUMN order_price VARCHAR(19);

ALTER TABLE live_smoke_approval
    ALTER COLUMN exchange SET NOT NULL,
    ALTER COLUMN order_division SET NOT NULL,
    ALTER COLUMN order_price SET NOT NULL,
    ADD CONSTRAINT live_smoke_approval_exchange_check CHECK (exchange = 'KRX'),
    ADD CONSTRAINT live_smoke_approval_order_division_check CHECK (order_division = '15'),
    ADD CONSTRAINT live_smoke_approval_order_price_check CHECK (order_price = '0');

COMMIT;

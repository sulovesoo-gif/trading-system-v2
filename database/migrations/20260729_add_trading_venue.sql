-- 기존 RAW 데이터의 거래소 출처를 확인할 수 없으므로 INTEGRATED로만 이관한다.
-- KRX 또는 NXT로의 세분 이관은 출처가 확인된 별도 작업에서만 수행한다.

DO $$
DECLARE
    table_name TEXT;
    key_name TEXT;
    time_column TEXT;
    instrument_column TEXT;
BEGIN
    FOR table_name, key_name, time_column, instrument_column IN
        VALUES
            ('raw_stock_quote', 'pk_raw_stock_quote', 'snapshot_time', 'stock_code'),
            ('raw_stock_execution', 'pk_raw_stock_execution', 'snapshot_time', 'stock_code'),
            ('raw_stock_minute', 'pk_raw_stock_minute', 'bar_time', 'stock_code'),
            ('raw_stock_daily', 'pk_raw_stock_daily', 'trade_date', 'stock_code'),
            ('raw_futures_quote', 'pk_raw_futures_quote', 'snapshot_time', 'futures_code'),
            ('raw_futures_minute', 'pk_raw_futures_minute', 'bar_time', 'futures_code')
    LOOP
        IF to_regclass(table_name) IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE %I ADD COLUMN IF NOT EXISTS trading_venue VARCHAR(10) NOT NULL DEFAULT ''INTEGRATED''',
                table_name
            );
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_' || table_name || '_trading_venue'
            ) THEN
                EXECUTE format(
                    'ALTER TABLE %I ADD CONSTRAINT %I CHECK (trading_venue IN (''KRX'', ''NXT'', ''INTEGRATED''))',
                    table_name, 'ck_' || table_name || '_trading_venue'
                );
            END IF;
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', table_name, key_name);
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I PRIMARY KEY (%I, data_source, market_code, trading_venue, collect_cycle, %I)',
                table_name, key_name, time_column, instrument_column
            );
        END IF;
    END LOOP;
END $$;

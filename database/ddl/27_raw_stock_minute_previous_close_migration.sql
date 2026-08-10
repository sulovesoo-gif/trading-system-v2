/* Non-destructive addition for the KIS official previous close carried in minute API output1. */
ALTER TABLE raw_stock_minute
    ADD COLUMN IF NOT EXISTS previous_close_price NUMERIC(18,2);

COMMENT ON COLUMN raw_stock_minute.previous_close_price IS
    '해당 거래일 KIS 기준 전일 종가(output1.stck_prdy_clpr). output2 분봉 행에는 없는 응답 공통 기준값.';

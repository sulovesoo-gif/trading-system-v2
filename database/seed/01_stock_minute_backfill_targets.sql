-- 1차 주식·ETF KRX 1분봉 백필 대상.
-- stock_master는 Collector 수집 대상의 유일한 기준이며, 이 파일은 명시적으로 실행할 때만 반영한다.
INSERT INTO stock_master
    (stock_code, stock_name, market, security_type, collect_yn, use_yn, sort_order, description)
VALUES
    ('000660', 'SK하이닉스', 'KOSPI', 'STOCK', 'Y', 'Y', 10, 'KRX 1분봉 백필 1차 대상'),
    ('0193T0', 'KODEX SK하이닉스단일종목레버리지', 'KOSPI', 'ETF', 'Y', 'Y', 20, 'KRX 1분봉 백필 1차 대상'),
    ('0197X0', 'SOL SK하이닉스단일종목인버스2X', 'KOSPI', 'ETF', 'Y', 'Y', 30, 'KRX 1분봉 백필 1차 대상'),
    ('005930', '삼성전자', 'KOSPI', 'STOCK', 'Y', 'Y', 40, 'KRX 1분봉 백필 1차 대상'),
    ('0193W0', 'KODEX 삼성전자단일종목레버리지', 'KOSPI', 'ETF', 'Y', 'Y', 50, 'KRX 1분봉 백필 1차 대상'),
    ('0193L0', 'PLUS 삼성전자선물인버스2X', 'KOSPI', 'ETF', 'Y', 'Y', 60, 'KRX 1분봉 백필 1차 대상')
ON CONFLICT (stock_code) DO NOTHING;

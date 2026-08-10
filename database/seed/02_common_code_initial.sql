/* 공통코드 초기값. 기존 값을 삭제하지 않고 코드 기준으로 갱신한다. */
INSERT INTO common_code_group (group_cd, group_name, description, attr1, attr2, attr3, attr4, attr5, attr6, attr7, attr8, attr9, attr10, use_yn)
VALUES
('STOCK', '종목 설정', '종목별 수집·분석·알림·매매 설정', '종목유형', '분봉수집여부', '일봉수집여부', '분석사용여부', '알림사용여부', '매매사용여부', '기본마켓코드', '연계기초종목코드', '표시순서', '예약', 'Y'),
('PRICE_FIELD', '가격 기준', '허용된 가격 계산 코드', '값분류', '안전 계산키', '진행봉사용여부', '예약', '예약', '예약', '예약', '예약', '예약', '예약', 'Y'),
('MA_CONFIG', '이동평균 설정', '1분 가격 계열 이동평균 기간 설정', '단기기간(분)', '중기기간(분)', '장기기간(분)', '가격기준코드', '이평유형', '진행봉포함여부', '사용여부보조', '예약', '예약', '예약', 'Y'),
('MARKET', '마켓 설정', '거래소별 수집·분석 설정', '수집사용여부', '분석사용여부', '매매사용여부', '시작시간', '종료시간', '세션공백시작', '세션공백종료', 'KIS마켓코드', '표시순서', '예약', 'Y'),
('API_SCHEDULE', 'API 호출주기', '실시간 수집 주기 설정', '주기단위', '주기값', '실행시', '실행분', '실행초', '시작시간', '종료시간', '사용여부', '작업유형', '예약', 'Y'),
('SYSTEM_SWITCH', '시스템 전역 스위치', '전역 실행 제어', '스위치값', '적용범위', '예약', '예약', '예약', '예약', '예약', '예약', '예약', '예약', 'Y'),
('STRATEGY', '전략 설정', '분석 전략별 사용 설정', '전략사용여부', '분석사용여부', '알림사용여부', '매매사용여부', '종목코드', '마켓코드', 'MA설정코드', '가격기준코드', '표시순서', '예약', 'Y')
ON CONFLICT (group_cd) DO UPDATE SET group_name = EXCLUDED.group_name, description = EXCLUDED.description, updated_at = CURRENT_TIMESTAMP;

INSERT INTO common_code (group_cd, code, code_name, sort_order, attr1, attr2, attr3, attr4, attr5, attr6, attr7, attr8, attr9, use_yn)
VALUES
('STOCK','000660','SK하이닉스',1,'STOCK','Y','Y','Y','Y','N','INTEGRATED',NULL,'1','Y'),
('STOCK','005930','삼성전자',2,'STOCK','Y','Y','Y','Y','N','INTEGRATED',NULL,'2','Y'),
('STOCK','0193T0','SK하이닉스 레버리지',3,'ETF','Y','Y','Y','Y','N','KRX','000660','3','Y'),
('STOCK','0197X0','SK하이닉스 인버스',4,'ETF','Y','Y','Y','Y','N','KRX','000660','4','Y'),
('PRICE_FIELD','OPEN','시가',1,'RAW','OPEN','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','HIGH','고가',2,'RAW','HIGH','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','LOW','저가',3,'RAW','LOW','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','CLOSE','종가',4,'RAW','CLOSE','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','HL2','고저평균',5,'DERIVED','HL2','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','HLC3','고저종평균',6,'DERIVED','HLC3','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','OHLC4','시고저종평균',7,'DERIVED','OHLC4','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('PRICE_FIELD','CURRENT_PRICE','진행 중 현재가',8,'RAW','CURRENT_PRICE','Y',NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('MA_CONFIG','MA_3_5_10','SMA 3·5·10',1,'3','5','10','CLOSE','SMA','Y','Y',NULL,NULL,'Y'),
('MARKET','KRX','한국거래소',1,'Y','Y','N','09:00','15:30',NULL,NULL,'J','1','Y'),
('MARKET','NXT','넥스트레이드',2,'Y','Y','N','08:00','20:00','08:50','08:59','NX','2','Y'),
('MARKET','INTEGRATED','통합거래소',3,'Y','Y','N','08:00','20:00','08:50','08:59','UN','3','Y'),
('API_SCHEDULE','STOCK_MINUTE_COMPLETE','완료 1분봉 수집',1,'MIN','1',NULL,NULL,'01','08:00','20:05','Y','COMPLETE_MINUTE','Y'),
('API_SCHEDULE','STOCK_MINUTE_SNAPSHOT','진행 1분봉 스냅샷',2,'SEC','5',NULL,NULL,NULL,'08:00','20:05','Y','IN_PROGRESS_SNAPSHOT','Y'),
('SYSTEM_SWITCH','GLOBAL_COLLECT_YN','전체수집',1,'Y','GLOBAL',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('SYSTEM_SWITCH','GLOBAL_ANALYSIS_YN','전체분석',2,'Y','GLOBAL',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('SYSTEM_SWITCH','GLOBAL_ALERT_YN','전체알림',3,'Y','GLOBAL',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('SYSTEM_SWITCH','GLOBAL_TRADE_YN','전체주식매매',4,'N','GLOBAL',NULL,NULL,NULL,NULL,NULL,NULL,NULL,'Y'),
('STRATEGY','MULTI_MA','다중 MA 분석',1,'Y','Y','N','N',NULL,'INTEGRATED','MA_3_5_10','CLOSE','1','Y')
ON CONFLICT (group_cd, code) DO UPDATE SET code_name = EXCLUDED.code_name, sort_order = EXCLUDED.sort_order,
attr1 = EXCLUDED.attr1, attr2 = EXCLUDED.attr2, attr3 = EXCLUDED.attr3, attr4 = EXCLUDED.attr4, attr5 = EXCLUDED.attr5,
attr6 = EXCLUDED.attr6, attr7 = EXCLUDED.attr7, attr8 = EXCLUDED.attr8, attr9 = EXCLUDED.attr9, use_yn = EXCLUDED.use_yn, updated_at = CURRENT_TIMESTAMP;

INSERT INTO common_code_group (group_cd, group_name, description, attr1, attr2, use_yn)
VALUES ('STOCK_DAILY', 'Daily official collection targets', 'Post-close official daily RAW targets', 'market code', 'trading venue', 'Y')
ON CONFLICT (group_cd) DO UPDATE SET group_name=EXCLUDED.group_name, description=EXCLUDED.description, updated_at=CURRENT_TIMESTAMP;

INSERT INTO common_code (group_cd, code, code_name, sort_order, attr1, attr2, use_yn)
VALUES ('STOCK_DAILY', '000660', 'SK hynix', 1, 'KOSPI', 'KRX', 'Y')
ON CONFLICT (group_cd, code) DO UPDATE SET code_name=EXCLUDED.code_name, sort_order=EXCLUDED.sort_order,
  attr1=EXCLUDED.attr1, attr2=EXCLUDED.attr2, use_yn=EXCLUDED.use_yn, updated_at=CURRENT_TIMESTAMP;

INSERT INTO common_code (group_cd, code, code_name, sort_order, attr1, attr2, attr5, attr6, attr7, attr8, attr9, use_yn)
VALUES ('API_SCHEDULE', 'STOCK_DAILY_CLOSE', 'Official daily collection', 5, 'MIN', '1', '05', '20:06', '20:06', 'Y', 'OFFICIAL_DAILY', 'Y')
ON CONFLICT (group_cd, code) DO UPDATE SET code_name=EXCLUDED.code_name, sort_order=EXCLUDED.sort_order,
  attr1=EXCLUDED.attr1, attr2=EXCLUDED.attr2, attr5=EXCLUDED.attr5, attr6=EXCLUDED.attr6,
  attr7=EXCLUDED.attr7, attr8=EXCLUDED.attr8, attr9=EXCLUDED.attr9, use_yn=EXCLUDED.use_yn, updated_at=CURRENT_TIMESTAMP;

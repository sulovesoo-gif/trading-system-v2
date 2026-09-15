-- Disposable local test schema only. Column names/types follow the inspected production schema.
CREATE TABLE flow_v3_strategy_master (
 strategy_id varchar(20) PRIMARY KEY,stock_code varchar(6),direction varchar(5),entry_family_code varchar(2),
 entry_fast_period smallint,entry_slow_period smallint,program_condition_code varchar(2),
 exit_fast_period smallint,exit_slow_period smallint,exit_policy_code varchar(20),execution_code varchar(6),is_enabled char(1)
);
CREATE TABLE flow_v3_paper_trade (
 strategy_id varchar(20),paper_trade_id bigint PRIMARY KEY,entry_signal_time timestamp,entry_execution_time timestamp,
 actual_exit_time timestamp,normal_exit_signal_time timestamp,exit_reason varchar(30),trade_status varchar(20)
);
CREATE TABLE flow_v3_paper_contract_correction (
 paper_trade_id bigint,audit_version text,corrected_exit_time timestamp,excluded_from_corrected_performance boolean
);
CREATE TABLE raw_stock_minute (
 stock_code varchar(6),bar_time timestamp,data_source varchar(20),trading_venue varchar(20),collect_cycle varchar(20),
 open_price numeric
);
CREATE TABLE raw_flow_execution (
 stock_code varchar(6),source_event_time timestamp,trading_venue varchar(20),tr_id varchar(20),
 current_price bigint,execution_volume bigint,execution_classification varchar(2),receive_sequence bigint,event_index smallint,
 source_gap_flag boolean,duplicate_flag boolean
);
CREATE TABLE raw_flow_program (
 stock_code varchar(6),source_event_time timestamp,received_at timestamp,trading_venue varchar(20),
 net_buy_execution_amount numeric,receive_sequence bigint,event_index smallint,source_gap_flag boolean,duplicate_flag boolean
);
CREATE TABLE raw_flow_orderbook_5s (
 stock_code varchar(6),source_event_time timestamp,received_at timestamp,trading_venue varchar(20),
 total_bid_quantity bigint,total_ask_quantity bigint,source_gap_flag boolean,duplicate_flag boolean
);
INSERT INTO common_code_group(group_cd,group_name) VALUES('MARKET','시장');
INSERT INTO common_code(group_cd,code,code_name,attr5) VALUES('MARKET','INTEGRATED','통합','20:00');
INSERT INTO common_code(group_cd,code,code_name,attr5) VALUES('MARKET','KRX','정규','15:30');
INSERT INTO flow_v3_strategy_master VALUES
 ('FV3000001','000660','LONG','F1',1,3,'P0',1,3,'SIGNAL_EOD','0193T0','Y'),
 ('FV3000002','005930','SHORT','F1',1,3,'P0',1,3,'SIGNAL_EOD','0193L0','Y'),
 ('FV3000003','005930','LONG','F1',1,3,'P0',1,3,'SIGNAL_HOLD','0193W0','N');
INSERT INTO flow_v3_paper_trade VALUES
 ('FV3000001',1,'2026-09-14 09:00','2026-09-14 09:01','2026-09-14 15:29',NULL,'SIGNAL_EOD','CLOSED');
INSERT INTO flow_v3_paper_contract_correction VALUES (1,'FLOW_V3_EOD_1519_V1','2026-09-14 15:19',false);
INSERT INTO raw_stock_minute VALUES
 ('000660','2026-09-14 09:01','KIS','KRX','1MIN',250000),
 ('000660','2026-09-14 09:01','KIS','KRX','1MIN',250000),
 ('000660','2026-09-14 09:01','KIS','INTEGRATED','1MIN',900000),
 ('000660','2026-09-14 15:19','KIS','KRX','1MIN',260000);
INSERT INTO raw_flow_execution VALUES
 ('000660','2026-09-14 09:00','KRX','H0STCNT0',250000,2,'1',1,0,false,false),
 ('000660','2026-09-14 09:01','KRX','H0STCNT0',250000,3,'5',2,0,false,false),
 ('000660','2026-09-14 16:00','KRX','H0STCNT0',260000,1,'1',3,0,false,false);
INSERT INTO raw_flow_program VALUES
 ('000660','2026-09-14 09:00','2026-09-14 09:00','KRX',100,1,0,false,false),
 ('000660','2026-09-14 09:01','2026-09-14 09:01','KRX',120,2,0,false,false);
INSERT INTO raw_flow_orderbook_5s VALUES
 ('000660','2026-09-14 09:01','2026-09-14 09:01','KRX',10,20,false,false),
 ('000660','2026-09-14 09:01:05','2026-09-14 09:01:05','KRX',11,21,false,false);

-- Insert only; never overwrite operator settings or the runtime attr1..7.
BEGIN;
INSERT INTO common_code_group(group_cd,group_name,attr1,attr2,attr3,attr4,attr5,attr6,attr7)
VALUES('FIRST_RISE_CAPACITY','FIRST_RISE capacity observation',
 'LIQUIDITY_5M_PARTICIPATION_PCT','SHADOW_FIXED_AMOUNT','SHORT_WINDOW_TRADES','LONG_WINDOW_TRADES',
 'WARNING_DEGRADATION_PCT','STRONG_WARNING_DEGRADATION_PCT','CRITICAL_DEGRADATION_PCT')
ON CONFLICT(group_cd) DO NOTHING;
INSERT INTO common_code(group_cd,code,code_name,attr1,attr2,attr3,attr4,attr5,attr6,attr7)
VALUES('FIRST_RISE_CAPACITY','DEFAULT','V2.0 capacity and Shadow','10','10000000','30','100','3','5','10')
ON CONFLICT(group_cd,code) DO NOTHING;
COMMIT;

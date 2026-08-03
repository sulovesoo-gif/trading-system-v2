/* Non-destructive program-trade collection settings. */
UPDATE common_code_group
SET attr10 = 'PROGRAM_COLLECT_YN', updated_at = CURRENT_TIMESTAMP
WHERE group_cd = 'STOCK';

UPDATE common_code SET attr10 = 'N', updated_at = CURRENT_TIMESTAMP
WHERE group_cd = 'STOCK';

UPDATE common_code SET attr10 = 'Y', updated_at = CURRENT_TIMESTAMP
WHERE group_cd = 'STOCK' AND code = '000660' AND use_yn = 'Y';

INSERT INTO common_code (group_cd, code, code_name, sort_order, attr1, attr2, attr3, attr4, attr5, attr6, attr7, attr8, attr9, attr10, use_yn)
VALUES ('API_SCHEDULE', 'STOCK_PROGRAM_1MIN', 'Program trade minute collection', 3, 'MIN', '1', NULL, NULL, '02', '08:00', '20:00', 'Y', 'RAW_COLLECT', NULL, 'Y')
ON CONFLICT (group_cd, code) DO UPDATE
SET attr1 = EXCLUDED.attr1, attr2 = EXCLUDED.attr2, attr5 = EXCLUDED.attr5,
    attr6 = EXCLUDED.attr6, attr7 = EXCLUDED.attr7, attr8 = EXCLUDED.attr8,
    attr9 = EXCLUDED.attr9, use_yn = EXCLUDED.use_yn, updated_at = CURRENT_TIMESTAMP;

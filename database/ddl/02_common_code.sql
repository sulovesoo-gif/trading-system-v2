/******************************************************************************
 * File Name  : 02_common_code.sql
 * Project    : Trading System V2
 * Description:
 *   공통코드 관리 테이블
 *
 * Rule:
 *   - 모든 변경 가능한 코드성 데이터를 관리한다.
 *   - 그룹별 attr1~attr10의 의미는 common_code_group을 참조한다.
 *   - 모든 시간 컬럼은 한국시간(KST, Asia/Seoul)을 기준으로 저장한다.
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS common_code;

CREATE TABLE common_code
(
    group_cd       VARCHAR(50)  NOT NULL,
    code           VARCHAR(100) NOT NULL,

    code_name      VARCHAR(200) NOT NULL,
    description    VARCHAR(500),
    sort_order     INTEGER      NOT NULL DEFAULT 0,

    attr1          VARCHAR(500),
    attr2          VARCHAR(500),
    attr3          VARCHAR(500),
    attr4          VARCHAR(500),
    attr5          VARCHAR(500),
    attr6          VARCHAR(500),
    attr7          VARCHAR(500),
    attr8          VARCHAR(500),
    attr9          VARCHAR(500),
    attr10         VARCHAR(500),

    use_yn         CHAR(1)      NOT NULL DEFAULT 'Y',

    created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by     VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by     VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',

    CONSTRAINT pk_common_code
        PRIMARY KEY (group_cd, code),

    CONSTRAINT fk_common_code_group
        FOREIGN KEY (group_cd)
        REFERENCES common_code_group(group_cd),

    CONSTRAINT ck_common_code_use_yn
        CHECK (use_yn IN ('Y', 'N'))
);

COMMENT ON TABLE common_code IS '공통코드';
COMMENT ON COLUMN common_code.group_cd IS '공통코드 그룹';
COMMENT ON COLUMN common_code.code IS '공통코드';
COMMENT ON COLUMN common_code.code_name IS '공통코드명';
COMMENT ON COLUMN common_code.description IS '설명';
COMMENT ON COLUMN common_code.sort_order IS '정렬순서';
COMMENT ON COLUMN common_code.attr1 IS '속성1';
COMMENT ON COLUMN common_code.attr2 IS '속성2';
COMMENT ON COLUMN common_code.attr3 IS '속성3';
COMMENT ON COLUMN common_code.attr4 IS '속성4';
COMMENT ON COLUMN common_code.attr5 IS '속성5';
COMMENT ON COLUMN common_code.attr6 IS '속성6';
COMMENT ON COLUMN common_code.attr7 IS '속성7';
COMMENT ON COLUMN common_code.attr8 IS '속성8';
COMMENT ON COLUMN common_code.attr9 IS '속성9';
COMMENT ON COLUMN common_code.attr10 IS '속성10';
COMMENT ON COLUMN common_code.use_yn IS '사용여부';
COMMENT ON COLUMN common_code.created_at IS '등록일시(KST)';
COMMENT ON COLUMN common_code.created_by IS '등록자';
COMMENT ON COLUMN common_code.updated_at IS '수정일시(KST)';
COMMENT ON COLUMN common_code.updated_by IS '수정자';

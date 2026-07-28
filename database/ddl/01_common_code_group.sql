/******************************************************************************
 * File Name  : 01_common_code_group.sql
 * Project    : Trading System V2
 * Description:
 *   공통코드 그룹 관리 테이블
 *
 * Rule:
 *   - 그룹별 attr1~attr10의 의미를 정의한다.
 *   - Common Code에서 동일한 attr 컬럼을 공유한다.
 *   - 모든 시간 컬럼은 한국시간(KST, Asia/Seoul)을 기준으로 저장한다.
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS common_code_group;

CREATE TABLE common_code_group
(
    group_cd       VARCHAR(50)  NOT NULL,
    group_name     VARCHAR(100) NOT NULL,
    description    VARCHAR(500),

    attr1          VARCHAR(100),
    attr2          VARCHAR(100),
    attr3          VARCHAR(100),
    attr4          VARCHAR(100),
    attr5          VARCHAR(100),
    attr6          VARCHAR(100),
    attr7          VARCHAR(100),
    attr8          VARCHAR(100),
    attr9          VARCHAR(100),
    attr10         VARCHAR(100),

    use_yn         CHAR(1)      NOT NULL DEFAULT 'Y',

    created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by     VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by     VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',

    CONSTRAINT pk_common_code_group
        PRIMARY KEY (group_cd),

    CONSTRAINT ck_common_code_group_use_yn
        CHECK (use_yn IN ('Y', 'N'))
);

COMMENT ON TABLE common_code_group IS '공통코드 그룹 관리';
COMMENT ON COLUMN common_code_group.group_cd IS '공통코드 그룹코드';
COMMENT ON COLUMN common_code_group.group_name IS '공통코드 그룹명';
COMMENT ON COLUMN common_code_group.description IS '그룹 설명';
COMMENT ON COLUMN common_code_group.attr1 IS 'attr1 용도';
COMMENT ON COLUMN common_code_group.attr2 IS 'attr2 용도';
COMMENT ON COLUMN common_code_group.attr3 IS 'attr3 용도';
COMMENT ON COLUMN common_code_group.attr4 IS 'attr4 용도';
COMMENT ON COLUMN common_code_group.attr5 IS 'attr5 용도';
COMMENT ON COLUMN common_code_group.attr6 IS 'attr6 용도';
COMMENT ON COLUMN common_code_group.attr7 IS 'attr7 용도';
COMMENT ON COLUMN common_code_group.attr8 IS 'attr8 용도';
COMMENT ON COLUMN common_code_group.attr9 IS 'attr9 용도';
COMMENT ON COLUMN common_code_group.attr10 IS 'attr10 용도';
COMMENT ON COLUMN common_code_group.use_yn IS '사용여부';
COMMENT ON COLUMN common_code_group.created_at IS '등록일시(KST)';
COMMENT ON COLUMN common_code_group.created_by IS '등록자';
COMMENT ON COLUMN common_code_group.updated_at IS '수정일시(KST)';
COMMENT ON COLUMN common_code_group.updated_by IS '수정자';

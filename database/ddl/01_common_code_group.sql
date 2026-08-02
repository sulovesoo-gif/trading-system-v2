/* 공통코드 그룹과 attr1~attr10의 의미를 정의한다. 비파괴 초기화용 DDL. */
SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS common_code_group
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
    CONSTRAINT pk_common_code_group PRIMARY KEY (group_cd),
    CONSTRAINT ck_common_code_group_use_yn CHECK (use_yn IN ('Y', 'N'))
);

COMMENT ON TABLE common_code_group IS '공통코드 그룹과 그룹별 속성 컬럼 정의';
COMMENT ON COLUMN common_code_group.attr1 IS '그룹별 attr1 의미';
COMMENT ON COLUMN common_code_group.attr10 IS '그룹별 attr10 의미';

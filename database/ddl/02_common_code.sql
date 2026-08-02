/* 변경 가능한 프로젝트 설정값. 기존 행을 삭제하지 않는 비파괴 DDL. */
SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS common_code
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
    CONSTRAINT pk_common_code PRIMARY KEY (group_cd, code),
    CONSTRAINT fk_common_code_group FOREIGN KEY (group_cd) REFERENCES common_code_group(group_cd),
    CONSTRAINT ck_common_code_use_yn CHECK (use_yn IN ('Y', 'N'))
);

COMMENT ON TABLE common_code IS '공통코드 실제 값';
COMMENT ON COLUMN common_code.attr1 IS '그룹 정의에 따른 속성값 1';
COMMENT ON COLUMN common_code.attr10 IS '그룹 정의에 따른 속성값 10';

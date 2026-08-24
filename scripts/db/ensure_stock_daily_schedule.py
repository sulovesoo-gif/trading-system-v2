"""Additive configuration repair for the official daily RAW scheduler."""
from __future__ import annotations
from src.repository.database import DatabaseSettings

def main() -> int:
    import psycopg
    settings = DatabaseSettings.from_environment()
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO common_code
                          (group_cd,code,code_name,sort_order,attr1,attr2,attr5,attr6,attr7,attr8,attr9,use_yn)
                          VALUES ('API_SCHEDULE','STOCK_DAILY_CLOSE','Official daily collection',5,
                                  'MIN','1','05','20:06','20:06','Y','OFFICIAL_DAILY','Y')
                          ON CONFLICT (group_cd,code) DO UPDATE SET
                            code_name=EXCLUDED.code_name,sort_order=EXCLUDED.sort_order,
                            attr1=EXCLUDED.attr1,attr2=EXCLUDED.attr2,attr5=EXCLUDED.attr5,
                            attr6=EXCLUDED.attr6,attr7=EXCLUDED.attr7,attr8=EXCLUDED.attr8,
                            attr9=EXCLUDED.attr9,use_yn=EXCLUDED.use_yn,updated_at=CURRENT_TIMESTAMP""")
        connection.commit()
    print("stock_daily_schedule=20:06_KST")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

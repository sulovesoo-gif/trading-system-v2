"""Single-session, asynchronous SQL analysis runner for the mobile dashboard.

The analysis connection always authenticates as a separately provisioned LOGIN
role.  PostgreSQL privileges, not SQL text inspection, enforce read-only access
to permanent objects while allowing TEMP objects in one persistent session.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr


MAX_EXCEL_ROWS = 1_048_576
MAX_EXCEL_CELL_CHARS = 32_767


@dataclass(frozen=True)
class SqlAnalysisSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    auth_token: str
    artifact_dir: Path
    session_ttl_seconds: int = 3600
    artifact_ttl_seconds: int = 3600
    max_sql_bytes: int = 5 * 1024 * 1024

    @classmethod
    def from_environment(cls, root: Path) -> "SqlAnalysisSettings":
        values = {
            "host": os.getenv("ANALYSIS_DB_HOST") or os.getenv("DB_HOST", ""),
            "port": os.getenv("ANALYSIS_DB_PORT") or os.getenv("DB_PORT", "5432"),
            "database": os.getenv("ANALYSIS_DB_NAME") or os.getenv("DB_NAME", ""),
            "user": os.getenv("ANALYSIS_DB_USER", ""),
            "password": os.getenv("ANALYSIS_DB_PASSWORD", ""),
            "auth_token": os.getenv("SQL_ANALYSIS_AUTH_TOKEN", ""),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError("SQL analysis runner is disabled; missing: " + ", ".join(missing))
        return cls(
            host=str(values["host"]), port=int(str(values["port"])), database=str(values["database"]),
            user=str(values["user"]), password=str(values["password"]), auth_token=str(values["auth_token"]),
            artifact_dir=Path(os.getenv("SQL_ANALYSIS_ARTIFACT_DIR", root / "var" / "sql-analysis")),
            session_ttl_seconds=int(os.getenv("SQL_ANALYSIS_SESSION_TTL_SECONDS", "3600")),
            artifact_ttl_seconds=int(os.getenv("SQL_ANALYSIS_ARTIFACT_TTL_SECONDS", "3600")),
            max_sql_bytes=int(os.getenv("SQL_ANALYSIS_MAX_SQL_BYTES", str(5 * 1024 * 1024))),
        )


def _excel_serial(value: date | datetime | dt_time) -> float:
    epoch = datetime(1899, 12, 30)
    if isinstance(value, dt_time):
        return (value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000) / 86400
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, dt_time.min)
    assert isinstance(value, datetime)
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return (value - epoch).total_seconds() / 86400


def _cell_xml(reference: str, value: Any) -> str:
    if value is None:
        return f'<c r="{reference}"/>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = float(value) if isinstance(value, Decimal) else value
        if isinstance(number, float) and not math.isfinite(number):
            text = str(number)
            return f'<c r="{reference}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'
        return f'<c r="{reference}"><v>{number}</v></c>'
    if isinstance(value, (datetime, date, dt_time)):
        style = 2 if isinstance(value, dt_time) else (1 if isinstance(value, date) and not isinstance(value, datetime) else 2)
        return f'<c r="{reference}" s="{style}"><v>{_excel_serial(value)}</v></c>'
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = str(value)[:MAX_EXCEL_CELL_CHARS]
    preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    return f'<c r="{reference}" t="inlineStr"><is><t{preserve}>{escape(text)}</t></is></c>'


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


class StreamingXlsxWriter:
    """Small dependency-free streaming writer for typed analysis result sets."""

    def __init__(self, destination: Path):
        self.destination = destination
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sql-xlsx-", dir=str(destination.parent)))
        self.sheets: list[tuple[str, Path, int, int]] = []

    def add_result(self, name: str, columns: list[str], rows) -> int:
        number = len(self.sheets) + 1
        path = self.temp_dir / f"sheet{number}.xml"
        count = 0
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
            handle.write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
            handle.write('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
            handle.write('<sheetData><row r="1">')
            for index, column in enumerate(columns, 1):
                handle.write(_cell_xml(f"{_column_name(index)}1", column).replace('<c ', '<c s="3" ', 1))
            handle.write('</row>')
            for count, row in enumerate(rows, 1):
                if count >= MAX_EXCEL_ROWS:
                    raise RuntimeError(f"Excel row limit exceeded in {name}")
                excel_row = count + 1
                handle.write(f'<row r="{excel_row}">')
                for index, value in enumerate(row, 1):
                    handle.write(_cell_xml(f"{_column_name(index)}{excel_row}", value))
                handle.write('</row>')
            handle.write('</sheetData><autoFilter ref="A1:')
            handle.write(f'{_column_name(max(1, len(columns)))}{max(1, count + 1)}"/>')
            handle.write('</worksheet>')
        safe_name = re.sub(r"[\\/*?:\[\]]", "_", name)[:31] or f"RESULT_{number:02d}"
        self.sheets.append((safe_name, path, count, len(columns)))
        return count

    def close(self) -> None:
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(self.destination, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                sheet_overrides = "".join(
                    f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                    for i in range(1, len(self.sheets) + 1)
                )
                archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + sheet_overrides + '</Types>')
                archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
                sheets = "".join(f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>' for i, (name, _, _, _) in enumerate(self.sheets, 1))
                archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + sheets + '</sheets></workbook>')
                relationships = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(self.sheets) + 1))
                relationships += f'<Relationship Id="rId{len(self.sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + relationships + '</Relationships>')
                archive.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font/><font><b/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>')
                for i, (_, path, _, _) in enumerate(self.sheets, 1):
                    archive.write(path, f"xl/worksheets/sheet{i}.xml")
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def abort(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.destination.unlink(missing_ok=True)


class SqlAnalysisRunner:
    def __init__(self, history_pool, settings: SqlAnalysisSettings):
        self.history_pool = history_pool
        self.settings = settings
        self.settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sql-analysis")
        self._lock = threading.RLock()
        self._connection = None
        self._session_id = uuid.uuid4()
        self._last_activity = time.monotonic()
        self._active_execution: uuid.UUID | None = None
        self._close_after_job = False
        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._janitor_loop, daemon=True, name="sql-analysis-janitor")
        self._janitor.start()

    @property
    def auth_token(self) -> str:
        return self.settings.auth_token

    def _record(self, sql: str, title: str | None, source_type: str, filename: str | None, request_key: str) -> uuid.UUID:
        execution_id = uuid.uuid4()
        payload = (execution_id, request_key, self._session_id, title, source_type, filename, sql,
                   hashlib.sha256(sql.encode("utf-8")).hexdigest(), len(sql.encode("utf-8")))
        with self.history_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO sql_analysis_execution_history
                (execution_id,request_key,analysis_session_id,research_title,source_type,original_filename,sql_text,sql_sha256,sql_size_bytes,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'QUEUED') ON CONFLICT (request_key) DO NOTHING
                RETURNING execution_id""", payload)
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT execution_id FROM sql_analysis_execution_history WHERE request_key=%s", (request_key,))
                return cur.fetchone()[0]
        return execution_id

    def _execution_for_request(self, request_key: str) -> uuid.UUID | None:
        with self.history_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT execution_id FROM sql_analysis_execution_history WHERE request_key=%s", (request_key,))
            row = cur.fetchone()
            return row[0] if row else None

    def submit(self, sql: str, title: str | None, source_type: str, filename: str | None, request_key: str) -> dict:
        encoded = sql.encode("utf-8")
        if not sql.strip():
            raise ValueError("SQL is empty")
        if len(encoded) > self.settings.max_sql_bytes:
            raise ValueError("SQL exceeds configured size limit")
        if source_type not in {"UPLOAD", "PASTE"}:
            raise ValueError("invalid source type")
        with self._lock:
            prior = self._execution_for_request(request_key)
            if prior is not None:
                return self.get_execution(str(prior))
            if self._active_execution is not None:
                raise RuntimeError("another SQL analysis job is already active")
            title = title or filename or f"SQL_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            execution_id = self._record(sql, title, source_type, filename, request_key)
            existing = self.get_execution(str(execution_id))
            self._active_execution = execution_id
            self._executor.submit(self._run, execution_id, sql)
            return self.get_execution(str(execution_id))

    def _connect(self):
        import psycopg
        with self._lock:
            if self._connection is None or self._connection.closed:
                self._connection = psycopg.connect(host=self.settings.host, port=self.settings.port,
                    dbname=self.settings.database, user=self.settings.user, password=self.settings.password,
                    autocommit=True, options="-c TimeZone=Asia/Seoul -c application_name=mobile_sql_analysis_runner")
            self._last_activity = time.monotonic()
            return self._connection

    def _run(self, execution_id: uuid.UUID, sql: str) -> None:
        started = time.monotonic()
        writer = None
        output = self.settings.artifact_dir / f"{execution_id}.xlsx"
        try:
            with self.history_pool.connection() as conn, conn.cursor() as cur:
                cur.execute("UPDATE sql_analysis_execution_history SET status='RUNNING',started_at=clock_timestamp(),updated_at=clock_timestamp() WHERE execution_id=%s", (execution_id,))
            analysis = self._connect()
            writer = StreamingXlsxWriter(output)
            summaries = []
            total_rows = 0
            with analysis.cursor() as cur:
                cur.execute(sql, prepare=False)
                statement_number = 0
                while True:
                    statement_number += 1
                    if cur.description is not None:
                        columns = [column.name for column in cur.description]
                        rows = iter(lambda: cur.fetchmany(2000), [])
                        flat_rows = (row for batch in rows for row in batch)
                        count = writer.add_result(f"RESULT_{len(summaries)+1:02d}", columns, flat_rows)
                        summaries.append({"result_no": len(summaries)+1, "statement_no": statement_number,
                                          "row_count": count, "column_count": len(columns), "columns": columns})
                        total_rows += count
                    if not cur.nextset():
                        break
            if not summaries:
                writer.add_result("RESULT_01", ["message"], [("SQL completed; no tabular result set",)])
                summaries.append({"result_no": 1, "statement_no": statement_number, "row_count": 1,
                                  "column_count": 1, "columns": ["message"]})
                total_rows = 1
            writer.close()
            duration = round((time.monotonic() - started) * 1000)
            filename = f"SQL_ANALYSIS_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(execution_id)[:8]}.xlsx"
            with self.history_pool.connection() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE sql_analysis_execution_history SET status='SUCCEEDED',finished_at=clock_timestamp(),
                    duration_ms=%s,result_set_count=%s,total_result_rows=%s,result_summary=%s::jsonb,
                    excel_filename=%s,excel_size_bytes=%s,updated_at=clock_timestamp() WHERE execution_id=%s""",
                    (duration, len(summaries), total_rows, json.dumps(summaries, ensure_ascii=False), filename, output.stat().st_size, execution_id))
        except Exception as error:
            if writer is not None:
                writer.abort()
            diagnostic = getattr(error, "diag", None)
            sqlstate = getattr(error, "sqlstate", None)
            position = getattr(diagnostic, "statement_position", None) if diagnostic else None
            context = getattr(diagnostic, "context", None) if diagnostic else None
            duration = round((time.monotonic() - started) * 1000)
            with self.history_pool.connection() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE sql_analysis_execution_history SET status='FAILED',finished_at=clock_timestamp(),
                    duration_ms=%s,error_sqlstate=%s,error_message=%s,error_statement_position=%s,error_context=%s,
                    updated_at=clock_timestamp() WHERE execution_id=%s""",
                    (duration, sqlstate, str(error)[:8000], int(position) if position else None,
                     str(context)[:8000] if context else None, execution_id))
            # A failed statement in autocommit normally leaves an idle usable
            # session.  Never retain an aborted/broken session: that would make
            # later TEMP results ambiguous.
            try:
                from psycopg.pq import TransactionStatus
                if self._connection is None or self._connection.closed or \
                        self._connection.info.transaction_status != TransactionStatus.IDLE:
                    with self._lock:
                        self._close_session_locked()
            except Exception:
                with self._lock:
                    self._close_session_locked()
        finally:
            with self._lock:
                self._active_execution = None
                self._last_activity = time.monotonic()
                if self._close_after_job:
                    self._close_session_locked()

    def _row_to_dict(self, row, columns) -> dict:
        result = dict(zip(columns, row))
        result["execution_id"] = str(result["execution_id"])
        result["analysis_session_id"] = str(result["analysis_session_id"])
        return result

    def get_execution(self, execution_id: str) -> dict:
        with self.history_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT execution_id,analysis_session_id,research_title,source_type,original_filename,sql_text,
                status,queued_at,started_at,finished_at,duration_ms,result_set_count,total_result_rows,result_summary,
                excel_filename,excel_size_bytes,error_sqlstate,error_message,error_statement_position,error_context
                FROM sql_analysis_execution_history WHERE execution_id=%s""", (execution_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError("execution not found")
            return self._row_to_dict(row, [column.name for column in cur.description])

    def recent(self, limit: int = 10) -> list[dict]:
        limit = max(1, min(30, limit))
        with self.history_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT execution_id,analysis_session_id,research_title,source_type,original_filename,
                status,queued_at,started_at,finished_at,duration_ms,result_set_count,total_result_rows,result_summary,
                excel_filename,excel_size_bytes,error_sqlstate,error_message,error_statement_position,error_context
                FROM sql_analysis_execution_history ORDER BY queued_at DESC LIMIT %s""", (limit,))
            columns = [column.name for column in cur.description]
            return [self._row_to_dict(row, columns) for row in cur.fetchall()]

    def status(self) -> dict:
        with self._lock:
            return {"enabled": True, "session_id": str(self._session_id),
                    "session_connected": bool(self._connection is not None and not self._connection.closed),
                    "session_ttl_seconds": self.settings.session_ttl_seconds,
                    "active_execution_id": str(self._active_execution) if self._active_execution else None}

    def artifact(self, execution_id: str) -> tuple[Path, str]:
        item = self.get_execution(execution_id)
        if item["status"] != "SUCCEEDED" or not item["excel_filename"]:
            raise KeyError("Excel result is not available")
        path = self.settings.artifact_dir / f"{execution_id}.xlsx"
        if not path.is_file():
            raise KeyError("Excel result expired")
        return path, item["excel_filename"]

    def end_session(self) -> dict:
        with self._lock:
            if self._active_execution is not None:
                self._close_after_job = True
                if self._connection is not None and not self._connection.closed:
                    self._connection.cancel()
                return {"ended": False, "pending": True}
            old = str(self._session_id)
            self._close_session_locked()
            return {"ended": True, "session_id": old}

    def _close_session_locked(self) -> None:
        if self._connection is not None and not self._connection.closed:
            self._connection.close()
        self._connection = None
        self._session_id = uuid.uuid4()
        self._close_after_job = False
        self._last_activity = time.monotonic()

    def _janitor_loop(self) -> None:
        while not self._stop.wait(30):
            cutoff = time.time() - self.settings.artifact_ttl_seconds
            for path in self.settings.artifact_dir.glob("*.xlsx"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except FileNotFoundError:
                    pass
            with self._lock:
                if self._active_execution is None and self._connection is not None and \
                        time.monotonic() - self._last_activity >= self.settings.session_ttl_seconds:
                    self._close_session_locked()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._close_session_locked()
        self._executor.shutdown(wait=False, cancel_futures=False)

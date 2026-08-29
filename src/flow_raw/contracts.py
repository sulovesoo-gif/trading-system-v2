"""Official KIS websocket field order and deterministic parsing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TR_EXECUTION = "H0STCNT0"
TR_PROGRAM = "H0STPGM0"
TR_ORDERBOOK = "H0STASP0"
SUPPORTED_TR_IDS = frozenset({TR_EXECUTION, TR_PROGRAM, TR_ORDERBOOK})

# Source: Korea Investment & Securities official open-trading-api websocket samples.
EXECUTION_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN",
    "PRDY_VRSS", "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC",
    "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL",
    "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU",
    "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CCLD_DVSN", "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
)

PROGRAM_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "SELN_CNTG_VOL", "SELN_TR_PBMN",
    "SHNU_CNTG_VOL", "SHNU_TR_PBMN", "NTBY_CNTG_VOL", "NTBY_TR_PBMN",
    "SELN_RSQN", "SHNU_RSQN", "WHOL_NTBY_RSQN",
)

ORDERBOOK_FIELDS = (
    "MKSC_SHRN_ISCD", "BSOP_HOUR", "HOUR_CLS_CODE",
    *(f"ASKP{i}" for i in range(1, 11)), *(f"BIDP{i}" for i in range(1, 11)),
    *(f"ASKP_RSQN{i}" for i in range(1, 11)), *(f"BIDP_RSQN{i}" for i in range(1, 11)),
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "OVTM_TOTAL_ASKP_RSQN",
    "OVTM_TOTAL_BIDP_RSQN", "ANTC_CNPR", "ANTC_CNQN", "ANTC_VOL",
    "ANTC_CNTG_VRSS", "ANTC_CNTG_VRSS_SIGN", "ANTC_CNTG_PRDY_CTRT",
    "ACML_VOL", "TOTAL_ASKP_RSQN_ICDC", "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC", "OVTM_TOTAL_BIDP_ICDC", "STCK_DEAL_CLS_CODE",
)

FIELDS = {TR_EXECUTION: EXECUTION_FIELDS, TR_PROGRAM: PROGRAM_FIELDS, TR_ORDERBOOK: ORDERBOOK_FIELDS}


class FlowContractError(ValueError):
    pass


@dataclass(frozen=True)
class WireEvent:
    tr_id: str
    values: dict[str, str]
    raw_record: str
    event_index: int

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(f"{self.tr_id}|{self.raw_record}".encode()).hexdigest()


def split_wire_frame(frame: str) -> list[WireEvent]:
    parts = frame.split("|", 3)
    if len(parts) != 4 or parts[0] not in {"0", "1"}:
        raise FlowContractError("not a KIS realtime data frame")
    tr_id = parts[1]
    if tr_id not in FIELDS:
        return []
    try:
        count = int(parts[2])
    except ValueError as error:
        raise FlowContractError("invalid realtime record count") from error
    names = FIELDS[tr_id]
    values = parts[3].split("^")
    expected = count * len(names)
    if len(values) != expected:
        raise FlowContractError(f"{tr_id} field count mismatch: expected={expected}, actual={len(values)}")
    events = []
    for index in range(count):
        record = values[index * len(names):(index + 1) * len(names)]
        events.append(WireEvent(tr_id, dict(zip(names, record)), "^".join(record), index))
    return events


def source_datetime(event: WireEvent, *, received_at: datetime) -> datetime:
    values = event.values
    hhmmss = values["STCK_CNTG_HOUR"] if event.tr_id != TR_ORDERBOOK else values["BSOP_HOUR"]
    business = values.get("BSOP_DATE")
    source_date = datetime.strptime(business, "%Y%m%d").date() if business else received_at.astimezone(KST).date()
    parsed_time = datetime.strptime(hhmmss[:6], "%H%M%S").time()
    return datetime.combine(source_date, parsed_time)


def as_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def as_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def five_second_bucket(value: datetime) -> datetime:
    return value.replace(second=(value.second // 5) * 5, microsecond=0)

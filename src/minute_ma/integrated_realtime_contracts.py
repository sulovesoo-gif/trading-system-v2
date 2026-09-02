"""Strict KIS H0UNCNT0 wire contract for the Minute MA INTEGRATED axis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

TR_INTEGRATED_EXECUTION = "H0UNCNT0"
INTEGRATED_SIGNAL_CODES = ("005930", "000660")

# Korea Investment open-trading-api: domestic_stock_functions_ws.ccnl_total.
INTEGRATED_EXECUTION_FIELDS = (
    "MKSC_SHRN_ISCD", "STCK_CNTG_HOUR", "STCK_PRPR", "PRDY_VRSS_SIGN",
    "PRDY_VRSS", "PRDY_CTRT", "WGHN_AVRG_STCK_PRC", "STCK_OPRC",
    "STCK_HGPR", "STCK_LWPR", "ASKP1", "BIDP1", "CNTG_VOL", "ACML_VOL",
    "ACML_TR_PBMN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU", "NTBY_CNTG_CSNU",
    "CTTR", "SELN_CNTG_SMTN", "SHNU_CNTG_SMTN", "CNTG_CLS_CODE", "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE", "OPRC_HOUR", "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR", "HGPR_HOUR", "HGPR_VRSS_PRPR_SIGN", "HGPR_VRSS_PRPR",
    "LWPR_HOUR", "LWPR_VRSS_PRPR_SIGN", "LWPR_VRSS_PRPR", "BSOP_DATE",
    "NEW_MKOP_CLS_CODE", "TRHT_YN", "ASKP_RSQN1", "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN", "TOTAL_BIDP_RSQN", "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL", "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE", "MRKT_TRTM_CLS_CODE", "VI_STND_PRC",
)


class IntegratedRealtimeContractError(ValueError):
    pass


@dataclass(frozen=True)
class IntegratedExecutionEvent:
    values: dict[str, str]
    raw_record: str
    event_index: int

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(
            f"{TR_INTEGRATED_EXECUTION}|{self.raw_record}".encode()
        ).hexdigest()


def split_integrated_execution_frame(frame: str) -> tuple[IntegratedExecutionEvent, ...]:
    parts = frame.split("|", 3)
    if len(parts) != 4 or parts[0] not in {"0", "1"}:
        raise IntegratedRealtimeContractError("not a KIS realtime data frame")
    if parts[1] != TR_INTEGRATED_EXECUTION:
        return ()
    try:
        count = int(parts[2])
    except ValueError as error:
        raise IntegratedRealtimeContractError("invalid realtime record count") from error
    values = parts[3].split("^")
    width = len(INTEGRATED_EXECUTION_FIELDS)
    if count <= 0 or len(values) != count * width:
        raise IntegratedRealtimeContractError(
            f"{TR_INTEGRATED_EXECUTION} field count mismatch: "
            f"expected_per_record={width}, records={count}, actual={len(values)}"
        )
    return tuple(
        IntegratedExecutionEvent(
            dict(zip(INTEGRATED_EXECUTION_FIELDS, values[index * width:(index + 1) * width])),
            "^".join(values[index * width:(index + 1) * width]),
            index,
        )
        for index in range(count)
    )


def integrated_source_datetime(event: IntegratedExecutionEvent, *, received_at: datetime) -> datetime:
    business = event.values.get("BSOP_DATE")
    source_date = datetime.strptime(business, "%Y%m%d").date() if business else received_at.date()
    source_time = datetime.strptime(event.values["STCK_CNTG_HOUR"][:6], "%H%M%S").time()
    return datetime.combine(source_date, source_time)


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

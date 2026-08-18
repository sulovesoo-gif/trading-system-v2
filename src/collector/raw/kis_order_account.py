"""Fail-closed account resolver for the official KIS cash-order payload."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Mapping


class KISOrderAccountConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class KISOrderAccount:
    cano: str
    account_product_code: str
    custtype: str = "P"

    @classmethod
    def from_environment(cls, values: Mapping[str, str] | None = None) -> "KISOrderAccount":
        source = environ if values is None else values
        cano = str(source.get("KIS_ACC_NO", ""))
        product = str(source.get("KIS_ACNT_PRDT_CD", ""))
        if len(cano) != 8 or not cano.isdigit():
            raise KISOrderAccountConfigurationError("KIS_ACC_NO must be an explicit eight-digit CANO")
        if len(product) != 2:
            raise KISOrderAccountConfigurationError("KIS_ACNT_PRDT_CD must be an explicit two-character product code")
        return cls(cano=cano, account_product_code=product)

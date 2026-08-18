import os
from decimal import Decimal
from src.forward.book import ForwardBookStatus,forward_book_cap_from_environment
def test_unset_cap_is_fail_closed(monkeypatch):
 monkeypatch.delenv('FORWARD_BOOK_CAP_KRW',raising=False)
 status=ForwardBookStatus(forward_book_cap_from_environment(),Decimal('0'))
 assert not status.actual_send_allowed and status.remaining_capacity is None
def test_cap_is_separate_numeric_exposure_contract(monkeypatch):
 monkeypatch.setenv('FORWARD_BOOK_CAP_KRW','1000')
 status=ForwardBookStatus(forward_book_cap_from_environment(),Decimal('400'))
 assert status.remaining_capacity==Decimal('600') and status.actual_send_allowed

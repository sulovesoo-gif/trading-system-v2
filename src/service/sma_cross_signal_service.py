"""완료 1분봉 SMA 크로스 상태와 이메일 알림을 조정한다."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.event.sma_cross_event import detect_cross_signal, threshold_break
from src.analysis.feature.sma_feature import MinuteBar, build_sma_features


class SmaCrossSignalService:
    def __init__(self, *, minute_repository, signal_repository, email_service=None) -> None:
        self.minute_repository = minute_repository
        self.signal_repository = signal_repository
        self.email_service = email_service

    def evaluate_completed_bar(self, *, stock_code: str, completed_time: datetime) -> str | None:
        bars = self.minute_repository.completed_bars(stock_code=stock_code, before_time=completed_time)
        features = build_sma_features(bars)
        if len(features) < 2:
            return None
        previous, current = features[-2], features[-1]
        if self.signal_repository.signal_exists_at(stock_code=stock_code, signal_time=current.bar.bar_time):
            return None
        candidate = self.signal_repository.active_candidate(stock_code)
        if candidate:
            baseline = self.signal_repository.latest_confirmed(stock_code)
            if baseline is None:
                raise RuntimeError("후보 타점에 직전 확정 타점이 없습니다.")
            break_direction, _ = threshold_break(current.bar.close_price, baseline.signal_price)
            if break_direction:
                alignment = "ALIGNED" if ((candidate.direction == "LONG") == (break_direction == "UP")) else "OPPOSED"
                confirmed = self.signal_repository.confirm_candidate(
                    signal_id=candidate.signal_id,
                    threshold_break_direction=break_direction,
                    threshold_direction_alignment=alignment,
                )
                self._finalize_previous_performance(baseline, current.bar.bar_time - timedelta(minutes=1))
                self.signal_repository.ensure_performance(confirmed.signal_id)
                self._save_related_bars(confirmed.signal_id, current.bar.bar_time)
                self._notify(confirmed, "CONFIRMED", current, baseline)
                return confirmed.status
            # 후보의 반대 방향 동시 크로스는 기존 후보가 무효화된 것으로 기록한다.
            event = detect_cross_signal(previous, current)
            if event and event.direction != candidate.direction:
                self.signal_repository.reject_candidate(signal_id=candidate.signal_id, reason="OPPOSITE_CROSS_BEFORE_THRESHOLD")
            elif event:
                return None

        event = detect_cross_signal(previous, current)
        if not event:
            return None
        previous_confirmed = self.signal_repository.latest_confirmed(stock_code)
        if previous_confirmed is None:
            created = self._create_signal(stock_code, previous, current, event, "INITIAL_CONFIRMED", None, True, None, None, None)
            self.signal_repository.ensure_performance(created.signal_id)
            self._save_related_bars(created.signal_id, current.bar.bar_time)
            self._notify(created, "INITIAL", current, None)
            return created.status

        closes = self.minute_repository.closes_since(
            stock_code=stock_code, start_time=previous_confirmed.signal_time, end_time=current.bar.bar_time
        )
        metrics = self._volatility_metrics(closes, previous_confirmed.signal_price)
        status = "CONFIRMED" if metrics["volatility_threshold_met"] else "CANDIDATE"
        created = self._create_signal(stock_code, previous, current, event, status, previous_confirmed, **metrics)
        self.signal_repository.ensure_performance(created.signal_id)
        self._save_related_bars(created.signal_id, current.bar.bar_time)
        if status == "CONFIRMED":
            self._finalize_previous_performance(previous_confirmed, current.bar.bar_time - timedelta(minutes=1))
            self._notify(created, "CONFIRMED", current, previous_confirmed)
        else:
            self._notify(created, "CANDIDATE", current, previous_confirmed)
        return created.status

    def update_open_performance(self, *, stock_code: str, completed_bar: MinuteBar) -> None:
        signals = (self.signal_repository.latest_confirmed(stock_code), self.signal_repository.active_candidate(stock_code))
        for signal in {item.signal_id: item for item in signals if item is not None}.values():
            if completed_bar.bar_time <= signal.signal_time:
                continue
            rate = completed_bar.close_price / signal.signal_price - Decimal("1")
            elapsed = int((completed_bar.bar_time - signal.signal_time).total_seconds() // 60)
            returns = {"last_evaluated_bar_time": completed_bar.bar_time}
            if elapsed in (1, 3, 5, 10):
                returns[f"return_after_{elapsed}m"] = rate
            returns["maximum_up_return_until_next_confirmed"] = max(Decimal("0"), rate)
            returns["maximum_down_return_until_next_confirmed"] = min(Decimal("0"), rate)
            self.signal_repository.update_performance(signal_id=signal.signal_id, returns=returns)

    def close_market_performance(self, *, stock_code: str, market_close_bar_time: datetime) -> None:
        signals = (self.signal_repository.latest_confirmed(stock_code), self.signal_repository.active_candidate(stock_code))
        for signal in {item.signal_id: item for item in signals if item is not None}.values():
            self.signal_repository.update_performance(
                signal_id=signal.signal_id,
                returns={"last_evaluated_bar_time": market_close_bar_time},
                end_reason="MARKET_CLOSE",
            )

    def _create_signal(self, stock_code, previous_feature, current, event, status, previous_confirmed, volatility_threshold_met, maximum_up_change_since_previous, maximum_down_change_since_previous, maximum_absolute_change_since_previous):
        return self.signal_repository.create({
            "signal_time": current.bar.bar_time, "stock_code": stock_code, "direction": event.direction, "status": status,
            "signal_price": current.bar.close_price, "candle_open": current.bar.open_price, "candle_close": current.bar.close_price,
            "candle_direction": event.candle_direction, "direction_alignment": event.direction_alignment,
            "sma5": current.sma5, "sma10": current.sma10, "previous_sma5": previous_feature.sma5,
            "previous_sma10": previous_feature.sma10,
            "previous_confirmed_signal_time": previous_confirmed.signal_time if previous_confirmed else None,
            "previous_confirmed_signal_price": previous_confirmed.signal_price if previous_confirmed else None,
            "maximum_up_change_since_previous": maximum_up_change_since_previous,
            "maximum_down_change_since_previous": maximum_down_change_since_previous,
            "maximum_absolute_change_since_previous": maximum_absolute_change_since_previous,
            "volatility_threshold_met": volatility_threshold_met,
        })

    @staticmethod
    def _volatility_metrics(closes, baseline: Decimal) -> dict[str, Decimal | bool]:
        changes = [close / baseline - Decimal("1") for close in closes]
        maximum_up = max(changes)
        maximum_down = min(changes)
        maximum_absolute = max(abs(item) for item in changes)
        return {"volatility_threshold_met": maximum_absolute >= Decimal("0.01"), "maximum_up_change_since_previous": maximum_up,
                "maximum_down_change_since_previous": maximum_down, "maximum_absolute_change_since_previous": maximum_absolute}

    def _finalize_previous_performance(self, signal, end_time: datetime) -> None:
        self.signal_repository.update_performance(signal_id=signal.signal_id, returns={"last_evaluated_bar_time": end_time}, end_reason="NEXT_CONFIRMED_SIGNAL")

    def _save_related_bars(self, signal_id: int, signal_time: datetime) -> None:
        for stock_code in ("0193T0", "0197X0"):
            bar = self.minute_repository.nearest_completed_bar(stock_code=stock_code, before_time=signal_time, trading_venue="KRX")
            if bar:
                self.signal_repository.save_related_bar(signal_id=signal_id, stock_code=stock_code, bar=bar)

    def _notify(self, signal, notification_type: str, feature, previous) -> None:
        if not self.signal_repository.create_notification(signal_id=signal.signal_id, notification_type=notification_type):
            return
        if self.email_service is None:
            return
        body = self._body(signal, self.signal_repository.signal_details(signal.signal_id))
        try:
            self.email_service.send(subject=f"[Trading V2] {signal.direction} {notification_type}", body=body)
        except Exception as error:
            self.signal_repository.mark_notification_failed(signal_id=signal.signal_id, notification_type=notification_type, message=type(error).__name__)
            return
        self.signal_repository.mark_notification_sent(signal_id=signal.signal_id, notification_type=notification_type)

    @staticmethod
    def _body(signal, details) -> str:
        return "\n".join((
            f"상태: {signal.status}", f"방향: {signal.direction}",
            f"타점 시각(KST): {signal.signal_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"현재 종가: {signal.signal_price}", f"SMA5: {details['sma5']}", f"SMA10: {details['sma10']}",
            f"직전 확정 타점 가격: {details['previous_price']}", f"직전 이후 최대 상승률: {details['maximum_up']}",
            f"직전 이후 최대 하락률: {details['maximum_down']}", f"1% 변동성 충족: {details['threshold_met']}",
            f"봉 방향 일치: {details['alignment']}", f"1% 경계 방향: {details['threshold_direction']}",
            f"경계-후보 방향 일치: {details['threshold_alignment']}",
        ))

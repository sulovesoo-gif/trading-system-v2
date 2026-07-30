"""Stateful ARMED SMA cross signal service using completed one-minute bars only."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.event.sma_cross_event import detect_close_cross, detect_ma_cross
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
        candidate = self.signal_repository.active_candidate(stock_code)
        ma_event = detect_ma_cross(previous, current)

        # The MA-cross bar only arms direction. It can never create a price signal itself.
        if ma_event:
            if candidate and candidate.direction != ma_event.direction:
                self.signal_repository.reject_candidate(signal_id=candidate.signal_id, reason="OPPOSITE_MA_CROSS")
                candidate = None
            self.signal_repository.upsert_arm(
                stock_code=stock_code,
                armed_direction=ma_event.direction,
                ma_cross_time=current.bar.bar_time,
                ma_cross_price=current.bar.close_price,
                ma_cross_sma5=current.sma5,
                ma_cross_sma10=current.sma10,
                preserve_candidate=candidate is not None,
            )
            return None

        armed = self.signal_repository.armed_state(stock_code)
        if armed is None:
            return None

        previous_confirmed = self.signal_repository.latest_confirmed(stock_code)
        if candidate:
            if previous_confirmed is None:
                raise RuntimeError("A candidate exists without a previous confirmed signal.")
            metrics = self._range_metrics(self.minute_repository.bars_since(
                stock_code=stock_code,
                start_time=previous_confirmed.signal_time,
                end_time=current.bar.bar_time,
            ))
            if metrics["volatility_threshold_met"]:
                confirmed = self.signal_repository.confirm_candidate(
                    signal_id=candidate.signal_id,
                    confirmed_time=current.bar.bar_time,
                    confirmed_price=current.bar.close_price,
                    range_metrics=metrics,
                )
                self.signal_repository.clear_arm(stock_code)
                self._finalize_previous_performance(previous_confirmed, current.bar.bar_time - timedelta(minutes=1))
                self.signal_repository.ensure_performance(confirmed.signal_id)
                self._save_related_bars(confirmed.signal_id, current.bar.bar_time)
                self._notify(confirmed, "CONFIRMED")
                return confirmed.status
            return None

        if current.bar.bar_time <= armed.ma_cross_time:
            return None
        if not detect_close_cross(previous, current, armed.armed_direction):
            return None
        if self.signal_repository.signal_exists_at(stock_code=stock_code, signal_time=current.bar.bar_time):
            return None

        if previous_confirmed is None:
            created = self._create_signal(
                stock_code=stock_code,
                previous_feature=previous,
                current=current,
                armed=armed,
                status="INITIAL_CONFIRMED",
                previous_confirmed=None,
                metrics=self._empty_range_metrics(),
            )
            self.signal_repository.clear_arm(stock_code)
            self.signal_repository.ensure_performance(created.signal_id)
            self._save_related_bars(created.signal_id, current.bar.bar_time)
            self._notify(created, "INITIAL")
            return created.status

        metrics = self._range_metrics(self.minute_repository.bars_since(
            stock_code=stock_code,
            start_time=previous_confirmed.signal_time,
            end_time=current.bar.bar_time,
        ))
        status = "CONFIRMED" if metrics["volatility_threshold_met"] else "CANDIDATE"
        created = self._create_signal(
            stock_code=stock_code,
            previous_feature=previous,
            current=current,
            armed=armed,
            status=status,
            previous_confirmed=previous_confirmed,
            metrics=metrics,
        )
        self.signal_repository.ensure_performance(created.signal_id)
        self._save_related_bars(created.signal_id, current.bar.bar_time)
        if status == "CONFIRMED":
            self.signal_repository.clear_arm(stock_code)
            self._finalize_previous_performance(previous_confirmed, current.bar.bar_time - timedelta(minutes=1))
            self._notify(created, "CONFIRMED")
        else:
            self.signal_repository.set_arm_candidate(stock_code=stock_code, signal_id=created.signal_id)
            self._notify(created, "CANDIDATE")
        return created.status

    def restore_armed_state(self, *, stock_code: str, before_time: datetime):
        """Restore only the latest valid MA cross after restart.

        This intentionally does not replay historical close crosses, create signals, or send alerts.
        The realtime runner processes only completed bars newer than its startup watermark.
        """
        existing = self.signal_repository.armed_state(stock_code)
        if existing is not None:
            return existing
        latest_confirmed = self.signal_repository.latest_confirmed(stock_code)
        bars = self.minute_repository.completed_bars(stock_code=stock_code, before_time=before_time)
        features = build_sma_features(bars)
        for previous, current in zip(reversed(features[:-1]), reversed(features[1:])):
            if latest_confirmed is not None and current.bar.bar_time <= latest_confirmed.signal_time:
                break
            event = detect_ma_cross(previous, current)
            if event is not None:
                return self.signal_repository.upsert_arm(
                    stock_code=stock_code,
                    armed_direction=event.direction,
                    ma_cross_time=current.bar.bar_time,
                    ma_cross_price=current.bar.close_price,
                    ma_cross_sma5=current.sma5,
                    ma_cross_sma10=current.sma10,
                )
        return None

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

    def _create_signal(self, *, stock_code, previous_feature, current, armed, status, previous_confirmed, metrics):
        wait_minutes = int((current.bar.bar_time - armed.ma_cross_time).total_seconds() // 60)
        return self.signal_repository.create({
            "signal_time": current.bar.bar_time,
            "stock_code": stock_code,
            "direction": armed.armed_direction,
            "status": status,
            "signal_price": current.bar.close_price,
            "candle_open": current.bar.open_price,
            "candle_close": current.bar.close_price,
            "candle_direction": self._candle_direction(current.bar),
            "direction_alignment": self._alignment(armed.armed_direction, current.bar),
            "sma5": current.sma5,
            "sma10": current.sma10,
            "previous_sma5": previous_feature.sma5,
            "previous_sma10": previous_feature.sma10,
            "previous_confirmed_signal_time": previous_confirmed.signal_time if previous_confirmed else None,
            "previous_confirmed_signal_price": previous_confirmed.signal_price if previous_confirmed else None,
            "armed_direction": armed.armed_direction,
            "ma_cross_time": armed.ma_cross_time,
            "ma_cross_price": armed.ma_cross_price,
            "ma_cross_sma5": armed.ma_cross_sma5,
            "ma_cross_sma10": armed.ma_cross_sma10,
            "armed_wait_minutes": wait_minutes,
            **metrics,
            # Retained research-only observations; they no longer decide confirmation.
            "maximum_up_change_since_previous": None,
            "maximum_down_change_since_previous": None,
            "maximum_absolute_change_since_previous": None,
            "confirmed_time": current.bar.bar_time if status in ("INITIAL_CONFIRMED", "CONFIRMED") else None,
            "confirmed_price": current.bar.close_price if status in ("INITIAL_CONFIRMED", "CONFIRMED") else None,
            "confirmed_change_from_previous": None,
        })

    @staticmethod
    def _range_metrics(bars: list[MinuteBar]) -> dict[str, object]:
        if not bars:
            raise ValueError("Range volatility requires at least one completed bar.")
        highest = max(bars, key=lambda bar: bar.close_price)
        lowest = min(bars, key=lambda bar: bar.close_price)
        close_range_return = highest.close_price / lowest.close_price - Decimal("1")
        return {
            "highest_close_since_previous": highest.close_price,
            "highest_close_time": highest.bar_time,
            "lowest_close_since_previous": lowest.close_price,
            "lowest_close_time": lowest.bar_time,
            "close_range_return": close_range_return,
            "volatility_threshold_met": close_range_return >= Decimal("0.01"),
        }

    @staticmethod
    def _empty_range_metrics() -> dict[str, object]:
        return {
            "highest_close_since_previous": None,
            "highest_close_time": None,
            "lowest_close_since_previous": None,
            "lowest_close_time": None,
            "close_range_return": None,
            "volatility_threshold_met": True,
        }

    def _finalize_previous_performance(self, signal, end_time: datetime) -> None:
        self.signal_repository.update_performance(
            signal_id=signal.signal_id,
            returns={"last_evaluated_bar_time": end_time},
            end_reason="NEXT_CONFIRMED_SIGNAL",
        )

    def _save_related_bars(self, signal_id: int, signal_time: datetime) -> None:
        for stock_code in ("0193T0", "0197X0"):
            bar = self.minute_repository.nearest_completed_bar(stock_code=stock_code, before_time=signal_time, trading_venue="KRX")
            if bar:
                self.signal_repository.save_related_bar(signal_id=signal_id, stock_code=stock_code, bar=bar)

    def _notify(self, signal, notification_type: str) -> None:
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
    def _candle_direction(bar: MinuteBar) -> str:
        return "UP" if bar.close_price > bar.open_price else "DOWN" if bar.close_price < bar.open_price else "FLAT"

    @classmethod
    def _alignment(cls, direction: str, bar: MinuteBar) -> str:
        candle_direction = cls._candle_direction(bar)
        if candle_direction == "FLAT":
            return "NEUTRAL"
        return "ALIGNED" if ((direction == "LONG") == (candle_direction == "UP")) else "OPPOSED"

    @staticmethod
    def _body(signal, details) -> str:
        lines = [
            f"상태: {signal.status}",
            f"방향: {signal.direction}",
            f"SMA 교차 시각(KST): {details['ma_cross_time'].strftime('%Y-%m-%d %H:%M:%S')}",
            f"SMA 교차 가격: {details['ma_cross_price']}",
            f"타점 시각(KST): {details['signal_time'].strftime('%Y-%m-%d %H:%M:%S')}",
            f"타점 종가: {details['signal_price']}",
            f"ARMED 대기(분): {details['armed_wait_minutes']}",
            f"직전 확정 타점 가격: {details['previous_price']}",
            f"최고 종가: {details['highest_close']} ({details['highest_close_time']})",
            f"최저 종가: {details['lowest_close']} ({details['lowest_close_time']})",
            f"종가 범위 수익률: {details['close_range_return']}",
            f"1% 종가 범위 충족: {details['threshold_met']}",
            f"봉 방향 일치: {details['alignment']}",
        ]
        if signal.status in ("INITIAL_CONFIRMED", "CONFIRMED"):
            lines.extend((
                f"실제 확정 시각(KST): {details['confirmed_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                f"실제 확정 종가: {details['confirmed_price']}",
            ))
        return "\n".join(lines)

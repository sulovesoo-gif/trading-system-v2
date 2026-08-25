import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from src.daily_ma_v03.evaluator import DailyMaStrategy
from src.service.daily_ma_dashboard_service import minute_cross_events
from scripts.dashboard.serve_multi_ma_dashboard import _daily_ma_as_of_date


class DailyMaDashboardContractTest(unittest.TestCase):
    @staticmethod
    def _minute_values(prices):
        base = datetime(2026, 8, 25, 9, 0)
        return [(base + timedelta(minutes=index), price) for index, price in enumerate(prices)]

    @staticmethod
    def _minute_strategy():
        return DailyMaStrategy("DS_TEST", "000660", "000660", "LONG", 3, 5, 3, 5, None, False)

    def test_1min_telemetry_counts_only_crossover_transitions(self):
        strategy = self._minute_strategy()
        cases = (
            ([10] * 8 + [20] * 8, 1, 0),       # below -> above -> hold
            ([20] * 8 + [10] * 8, 0, 1),       # above -> below -> hold
            ([10] * 8 + [20] * 8 + [10] * 8 + [20] * 8, 2, 1),
        )
        for prices, expected_entries, expected_exits in cases:
            events = minute_cross_events(self._minute_values(prices), strategy=strategy)
            self.assertEqual(sum(kind == "ENTRY" for _at, kind in events), expected_entries)
            self.assertEqual(sum(kind == "EXIT" for _at, kind in events), expected_exits)
            self.assertEqual(len(events), len(set(events)))

    def test_1min_telemetry_is_repeat_and_restart_deterministic(self):
        values = self._minute_values([10] * 8 + [20] * 8 + [10] * 8)
        first = minute_cross_events(values, strategy=self._minute_strategy())
        second = minute_cross_events(values, strategy=self._minute_strategy())
        self.assertEqual(first, second)

    def test_dashboard_is_read_only_consumer_of_runtime_state(self):
        source = Path('src/service/daily_ma_dashboard_service.py').read_text(encoding='utf-8')
        self.assertIn('vw_daily_strategy_selection_dashboard', source)
        self.assertIn('daily_strategy_live_risk_state', source)
        self.assertIn('daily_strategy_compound_capital', source)
        self.assertIn('daily_strategy_paper_event', source)
        self.assertNotIn('INSERT INTO', source)
        self.assertNotIn('UPDATE ', source)

    def test_grid_contract_keeps_all_three_universes_and_telemetry(self):
        source = Path('src/service/daily_ma_dashboard_service.py').read_text(encoding='utf-8')
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        for value in ('ALL', 'SELECTED', 'LIVE', 'ENTRY_NEAR', 'EXIT_NEAR', 'minute_telemetry'):
            self.assertIn(value, source + page)
        self.assertIn('/daily-ma/api/dashboard', page)
        self.assertIn('/daily-ma/api/detail', page)

    def test_detail_contract_exposes_actual_send_lifecycle_without_writes(self):
        source = Path('src/service/daily_ma_dashboard_service.py').read_text(encoding='utf-8')
        for name in (
            'daily_strategy_live_order_intent', 'daily_strategy_live_order_request',
            'live_broker_order', 'daily_strategy_live_fill_checkpoint',
            'execution_logical_position', 'daily_strategy_live_broker_cost_allocation',
            'daily_strategy_live_capital_settlement',
        ):
            self.assertIn(name, source)

    def test_runtime_server_exposes_read_only_daily_ma_routes(self):
        source = Path('scripts/dashboard/serve_multi_ma_dashboard.py').read_text(encoding='utf-8')
        self.assertIn('parsed.path == "/daily-ma/api/dashboard"', source)
        self.assertIn('parsed.path == "/daily-ma/api/detail"', source)
        self.assertIn('elif parsed.path == "/daily-ma"', source)

    def test_ui_uses_official_symbol_names_and_single_column_detail_sections(self):
        service = Path('src/service/daily_ma_dashboard_service.py').read_text(encoding='utf-8')
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        self.assertIn("group_cd='STOCK'", service)
        self.assertIn('signal_name', service)
        self.assertIn('execution_name', service)
        self.assertIn('id="symbolMode"', page)
        self.assertIn("dailyMaSymbolMode", page)
        self.assertNotIn('detail-grid', page)

    def test_ui_is_null_safe_and_uses_minute_aligned_fail_safe_refresh(self):
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        self.assertIn('Number.isFinite', page)
        self.assertIn('nextMinuteRefresh', page)
        self.assertIn("t.hour>=8&&t.hour<20", page)
        self.assertIn('id="autoRefresh"', page)
        self.assertIn('id="refreshNow"', page)
        self.assertIn('inFlight=false', page)
        self.assertIn('!inFlight&&Date.now()>=next', page)
        self.assertIn("loading?t('loading')", page)
        self.assertIn('lastError=', page)
        self.assertIn("'}[c]));", page)
        self.assertIn('마지막 정상 데이터 유지', page)
        self.assertNotIn("payload={rows:[]}", page)
        self.assertNotIn('NaN', page)

    def test_ui_keeps_symbol_and_display_language_modes_independent(self):
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        self.assertIn('id="symbolMode"', page)
        self.assertIn('id="languageMode"', page)
        self.assertIn('dailyMaSymbolMode', page)
        self.assertIn('dailyMaLanguageMode', page)
        self.assertIn('Code / English', page)
        self.assertIn('strategyLabel', page)

    def test_ui_defaults_to_live_and_localizes_operator_facing_detail_terms(self):
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        self.assertIn("let universe='LIVE'", page)
        self.assertIn('data-u="LIVE" class="active"', page)
        self.assertIn("orders:'주문 흐름'", page)
        self.assertIn("ownership:'귀속'", page)
        self.assertIn("cost:'비용'", page)
        self.assertIn("settlement:'정산'", page)
        self.assertIn("historical:'과거 기록'", page)
        self.assertIn("empty:'현재 없음'", page)
        self.assertIn('fieldLabel(k)', page)
        self.assertIn('if(detailPayload)renderDetail()', page)

    def test_dashboard_date_parameter_defaults_and_rejects_future_dates(self):
        self.assertEqual(_daily_ma_as_of_date({'date': ['2026-08-24']}), date(2026, 8, 24))
        self.assertEqual(_daily_ma_as_of_date({}), datetime.now().astimezone().date())
        with self.assertRaises(ValueError):
            _daily_ma_as_of_date({'date': ['2999-01-01']})

    def test_dashboard_date_is_kst_scoped_and_past_disables_auto_refresh(self):
        service = Path('src/service/daily_ma_dashboard_service.py').read_text(encoding='utf-8')
        server = Path('scripts/dashboard/serve_multi_ma_dashboard.py').read_text(encoding='utf-8')
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        self.assertIn('as_of_date', service)
        self.assertIn('_daily_ma_as_of_date', server)
        self.assertIn('id="dashboardDate"', page)
        self.assertIn('selectedDate=kstDateString()', page)
        self.assertIn("+'&date='+encodeURIComponent", page)
        self.assertIn('!isToday()', page)
        self.assertIn('auto&&isToday()&&autoWindow()', page)
        self.assertIn("pastDate:'과거 날짜 · 자동갱신 중지'", page)


if __name__ == '__main__':
    unittest.main()

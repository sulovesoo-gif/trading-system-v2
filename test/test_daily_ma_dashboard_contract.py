import unittest
from pathlib import Path


class DailyMaDashboardContractTest(unittest.TestCase):
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

    def test_ui_is_null_safe_and_uses_read_only_30_second_refresh(self):
        page = Path('reports/multi-ma/daily-ma.html').read_text(encoding='utf-8')
        self.assertIn('Number.isFinite', page)
        self.assertIn('next=Date.now()+30000', page)
        self.assertIn('id="autoRefresh"', page)
        self.assertIn('id="refreshNow"', page)
        self.assertNotIn('NaN', page)


if __name__ == '__main__':
    unittest.main()

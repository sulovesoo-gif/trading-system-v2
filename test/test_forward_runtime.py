from datetime import datetime

from src.forward import ForwardCandidate, ForwardExecutionPath, ForwardObservationRuntime


def candidate(active=True):
    return ForwardCandidate("C1", "LIVE_STRATEGY_5", ForwardExecutionPath("S1", "E1", "0193W0"), "005930", "approved", datetime(2026, 8, 19), "operator", active)


class Registry:
    def __init__(self): self.items = ()
    def active_candidates(self): return self.items


def test_candidate_zero_is_healthy_and_never_plans():
    plans = []
    runtime = ForwardObservationRuntime(registry=Registry(), evaluator=lambda *_: True, planner=plans.append)
    assert runtime.run_cycle(at=datetime(2026, 8, 19, 10)) == ()
    assert plans == []


def test_reload_recognizes_candidate_without_deploy_and_forces_one_share_no_send():
    registry = Registry(); plans = []
    runtime = ForwardObservationRuntime(registry=registry, evaluator=lambda *_: True, planner=plans.append)
    registry.items = (candidate(),)
    result = runtime.run_cycle(at=datetime(2026, 8, 19, 10))
    assert result[0].quantity == 1
    assert result[0].broker_send_eligible is False
    assert result[0].execution_stock_code == "0193W0"

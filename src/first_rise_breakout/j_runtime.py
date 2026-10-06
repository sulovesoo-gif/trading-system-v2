"""J market-only runtime. Actual execution outcomes are deliberately not inputs."""
import logging
from dataclasses import replace
from datetime import time

from .j_raw_tracking import market_needs_minutes
from .j_signal import JSignalEngine, JStep, JState
from .models import CandidateState, ResearchState
from .strategy import FirstRiseBreakoutStrategy

LOGGER=logging.getLogger(__name__)


class JMarketRuntime:
    def __init__(self, *, repository, minute_source):
        self.repository,self.minute_source=repository,minute_source
        self.minute_source.audit_previous_close=False
        self.states={}
        self.last_summary={}
        self.recovery_from={}
        self.rebuild=set()

    def restore(self, *, at):
        self.states={s.tracking.stock_code:(s,v,d) for s,v,d in self.repository.roster(business_date=at.date())}
        self.recovery_from={code:at for code in self.states}
        self.rebuild={code for code,(s,_,_) in self.states.items() if s.sequence==0}

    def register(self, candidate, *, discovered_at):
        # Legacy PAPER state is not the J lifecycle source of truth.
        seed=CandidateState(candidate.candidate_event_id,candidate.business_date,
                            candidate.stock_code,ResearchState.DISCOVERED)
        state,revision=self.repository.load_or_create(seed)
        if seed.stock_code not in self.states:
            self.recovery_from[seed.stock_code]=discovered_at
            if state.sequence==0:self.rebuild.add(seed.stock_code)
        else:
            discovered_at=self.states[seed.stock_code][2]
        self.states[seed.stock_code]=(state,revision,discovered_at)

    def refresh(self, *, at, config):
        count=0
        self.last_summary={'active':0,'open':0,'targets':0,'errors':0}
        ordered=sorted(self.states, key=lambda code:self.states[code][0].open_signal is None)
        for code in ordered:
            state,revision,discovered=self.states[code]
            try:
                cutoff=max(config.paper_entry_cutoff,config.live_entry_cutoff) if config else time(0)
                if not market_needs_minutes(state,at=at,cutoff=cutoff):
                    if state.open_signal is None:
                        self.minute_source.discard(stock_code=code,business_date=state.tracking.business_date)
                    continue
                self.last_summary['active']+=1
                self.last_summary['open']+=int(state.open_signal is not None)
                self.last_summary['targets']+=1
                bars=self.minute_source.completed_bars_from_open(stock_code=code,as_of=at)
                bars=sorted((b for b in bars if b.bar_time.date()==at.date()
                    and b.bar_time<at.replace(second=0,microsecond=0)),key=lambda b:b.bar_time)
                engine=JSignalEngine(config) if config is not None else None
                if engine is not None:
                    if code in self.rebuild:
                        # Repair old price-only bootstrap snapshots, but never
                        # overwrite an already persisted FIRST/SECOND lifecycle.
                        state=JState(CandidateState(state.tracking.candidate_event_id,
                            state.tracking.business_date,code,ResearchState.DISCOVERED))
                        self.rebuild.discard(code)
                    boundary=max(discovered,self.recovery_from.get(code,discovered)).replace(second=0,microsecond=0)
                    for bar in bars:
                        step=engine.advance(state,bar,completed_bars=bars,
                            bootstrap=bar.bar_time<boundary)
                        if step.state!=state or step.market_entry or step.market_exit:
                            revision=self.repository.save(step,expected_revision=revision)
                            state=step.state;count+=1
                            self.states[code]=(state,revision,discovered)
                    step=engine.session_close(state,bars,at=at)
                else:
                    # Invalid ENTRY config must not suppress EXIT.
                    step=JStep(state)
                    if state.open_signal is not None:
                        formula=FirstRiseBreakoutStrategy()
                        decision=formula.exit_from_completed_bars(state.open_signal,bars,
                            session_ended=at.time()>formula.SESSION_CLOSE)
                        if decision.create_exit:
                            step=JStep(replace(state,open_signal=None,prior_exit_reason=decision.reason,
                                prior_exit_time=decision.after.last_observed_at),market_exit=decision)
                if step.market_exit:
                    revision=self.repository.save(step,expected_revision=revision)
                    state=step.state;count+=1
                self.states[code]=(state,revision,discovered)
            except Exception as error:
                self.last_summary['errors']+=1
                LOGGER.exception('FIRST_RISE_J_REFRESH_ERROR stock_code=%s candidate_event_id=%s state=%s exception_type=%s error=%s',
                    code,state.tracking.candidate_event_id,state.tracking.state.value,type(error).__name__,error)
                # Refresh from durable state next cycle, including CAS conflicts.
                try:
                    restored,version=self.repository.load_or_create(state.tracking)
                    self.states[code]=(restored,version,discovered)
                    if restored.sequence==0:self.rebuild.add(code)
                except Exception:
                    LOGGER.exception('FIRST_RISE_J_RELOAD_ERROR stock_code=%s',code)
        return count

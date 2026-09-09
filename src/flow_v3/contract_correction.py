"""Overlay approved historical corrections without rewriting source PAPER rows."""
from datetime import time

CONTRACT = '15:18_SIGNAL / 15:19_EXECUTION'
VERSION = 'FLOW_V3_EOD_1519_V1'


def corrected_trades(trades, corrections):
    result = []
    for source in trades:
        correction = corrections.get(source['paper_trade_id'])
        if not correction:
            result.append(dict(source))
            continue
        if correction['excluded_from_corrected_performance']:
            if source['entry_signal_time'].time() <= time(15, 18):
                raise ValueError('EXCLUSION_BEFORE_CUTOFF')
            continue
        row = dict(source)
        row['actual_exit_time'] = correction['corrected_exit_time']
        row['actual_exit_price'] = correction['corrected_exit_price']
        row['normal_exit_signal_time'] = None
        row['trade_status'] = 'CLOSED'
        if row['actual_exit_time'] < row['entry_execution_time']:
            raise ValueError('CORRECTED_EXIT_BEFORE_ENTRY')
        result.append(row)
    return result

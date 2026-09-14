"""Gateway compatibility only. Synthetic frames and mocked persistence; no network/DB."""
import hashlib
import unittest
from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID

from src.flow_raw.contracts import (
    EXECUTION_FIELDS, EXECUTION_GATEWAY_47_FIELDS, ORDERBOOK_FIELDS,
    ORDERBOOK_GATEWAY_62_FIELDS, ORDERBOOK_GATEWAY_63_FIELDS, PROGRAM_FIELDS,
    TR_EXECUTION, TR_ORDERBOOK, TR_PROGRAM, FlowContractError, split_wire_frame,
    source_datetime,
)
from src.minute_ma.integrated_realtime_contracts import (
    INTEGRATED_EXECUTION_FIELDS, INTEGRATED_EXECUTION_GATEWAY_47_FIELDS,
    TR_INTEGRATED_EXECUTION, IntegratedRealtimeContractError,
    split_integrated_execution_frame, integrated_source_datetime,
)


def wire(tr, rows):
    return f"0|{tr}|{len(rows):03d}|" + "^".join(value for row in rows for value in row)


class TrailingFieldTest(unittest.TestCase):
    def assert_variants(self, tr, parser, variants):
        for names in variants:
            for count in (1, 2, 3):
                with self.subTest(tr=tr, width=len(names), records=count):
                    rows=[[f"record{n}:index{i}" for i in range(len(names))] for n in range(count)]
                    # Empty final values are still fields, not trimmed separators.
                    rows[-1][-1]=""
                    events=parser(wire(tr,rows))
                    self.assertEqual(len(events),count)
                    for n,(event,row) in enumerate(zip(events,rows)):
                        self.assertEqual(event.event_index,n)
                        self.assertEqual(list(event.values),list(names))
                        self.assertEqual(event.values,dict(zip(names,row)))
                        self.assertEqual(event.raw_record,"^".join(row))
                        self.assertEqual(event.payload_hash,hashlib.sha256(f"{tr}|{event.raw_record}".encode()).hexdigest())

    def test_execution_46_47_all_record_counts(self):
        self.assert_variants(TR_EXECUTION,split_wire_frame,(EXECUTION_FIELDS,EXECUTION_GATEWAY_47_FIELDS))

    def test_integrated_46_47_all_record_counts(self):
        self.assert_variants(TR_INTEGRATED_EXECUTION,split_integrated_execution_frame,
                             (INTEGRATED_EXECUTION_FIELDS,INTEGRATED_EXECUTION_GATEWAY_47_FIELDS))

    def test_orderbook_59_62_63_all_record_counts(self):
        self.assert_variants(TR_ORDERBOOK,split_wire_frame,
                             (ORDERBOOK_FIELDS,ORDERBOOK_GATEWAY_62_FIELDS,ORDERBOOK_GATEWAY_63_FIELDS))

    def test_exact_prefix_mapping_and_trailing_only(self):
        for tr,parser,old,new in (
            (TR_EXECUTION,split_wire_frame,EXECUTION_FIELDS,EXECUTION_GATEWAY_47_FIELDS),
            (TR_INTEGRATED_EXECUTION,split_integrated_execution_frame,INTEGRATED_EXECUTION_FIELDS,INTEGRATED_EXECUTION_GATEWAY_47_FIELDS),
            (TR_ORDERBOOK,split_wire_frame,ORDERBOOK_GATEWAY_62_FIELDS,ORDERBOOK_GATEWAY_63_FIELDS),
        ):
            with self.subTest(tr=tr):
                self.assertEqual(new[:-1],old)
                self.assertEqual(new[-1],f"KIS_UNDOCUMENTED_FIELD_{len(new)}")
                values=[f"index:{i}" for i in range(len(old))]
                prior=parser(wire(tr,[values]))[0]
                extended=parser(wire(tr,[values+['UNINTERPRETED']]))[0]
                self.assertEqual({k:extended.values[k] for k in old},prior.values)
                self.assertEqual(extended.values[new[-1]],'UNINTERPRETED')
                self.assertNotEqual(prior.payload_hash,extended.payload_hash)

    def test_unsupported_widths_and_record_counts_fail_closed(self):
        for tr,parser,error,widths in (
            (TR_EXECUTION,split_wire_frame,FlowContractError,(45,48,49)),
            (TR_INTEGRATED_EXECUTION,split_integrated_execution_frame,IntegratedRealtimeContractError,(45,48,49)),
            (TR_ORDERBOOK,split_wire_frame,FlowContractError,(58,60,61,64)),
        ):
            for width in widths:
                for count in (1,2,3):
                    with self.subTest(tr=tr,width=width,count=count),self.assertRaises(error):
                        parser(wire(tr,[['0']*width for _ in range(count)]))
            for count in ('0','-1','bad','2'):
                with self.subTest(tr=tr,count=count),self.assertRaises(error):
                    parser(f"0|{tr}|{count}|"+'^'.join(['0']*(63 if tr==TR_ORDERBOOK else 47)))

    def test_program_unchanged(self):
        self.assert_variants(TR_PROGRAM,split_wire_frame,(PROGRAM_FIELDS,))
        with self.assertRaises(FlowContractError):
            split_wire_frame(wire(TR_PROGRAM,[['0']*12]))

    def test_source_datetime_unchanged(self):
        for tr,parser,clock,old,new in (
            (TR_EXECUTION,split_wire_frame,source_datetime,EXECUTION_FIELDS,EXECUTION_GATEWAY_47_FIELDS),
            (TR_INTEGRATED_EXECUTION,split_integrated_execution_frame,integrated_source_datetime,INTEGRATED_EXECUTION_FIELDS,INTEGRATED_EXECUTION_GATEWAY_47_FIELDS),
            (TR_ORDERBOOK,split_wire_frame,source_datetime,ORDERBOOK_GATEWAY_62_FIELDS,ORDERBOOK_GATEWAY_63_FIELDS),
        ):
            record={k:'100' for k in old}
            record.update(BSOP_DATE='20260914',STCK_CNTG_HOUR='091503',BSOP_HOUR='091503')
            values=[record[k] for k in old]
            for row in (values,values+['unknown']):
                self.assertEqual(clock(parser(wire(tr,[row]))[0],received_at=datetime(2026,9,14,9,15,4)),datetime(2026,9,14,9,15,3))

    def test_repository_semantic_params_unchanged_raw_is_lossless(self):
        from src.flow_raw.repository import FlowRawRepository
        from src.minute_ma.integrated_realtime_repository import MinuteMaIntegratedRealtimeRepository
        for tr,parser,repository,old,hash_index in (
            (TR_EXECUTION,split_wire_frame,FlowRawRepository,EXECUTION_FIELDS,14),
            (TR_ORDERBOOK,split_wire_frame,FlowRawRepository,ORDERBOOK_GATEWAY_62_FIELDS,12),
            (TR_INTEGRATED_EXECUTION,split_integrated_execution_frame,MinuteMaIntegratedRealtimeRepository,INTEGRATED_EXECUTION_FIELDS,12),
        ):
            values={k:'100' for k in old}
            values.update(MKSC_SHRN_ISCD='000660',STCK_CNTG_HOUR='091503',BSOP_HOUR='091503',BSOP_DATE='20260914')
            rows=([values[k] for k in old],[values[k] for k in old]+['unknown'])
            results=[]
            for row in rows:
                cursor=MagicMock();connection=MagicMock();pool=MagicMock()
                connection.cursor.return_value.__enter__.return_value=cursor
                pool.connection.return_value.__enter__.return_value=connection
                event=parser(wire(tr,[row]))[0]
                repository(pool).save_event(event,received_at=datetime(2026,9,14,9,15,4),
                    connection_id=UUID(int=1),collector_instance_id=UUID(int=2),receive_sequence=123,
                    reconnect_flag=False,source_gap_flag=False,event_time_regression_flag=False,duplicate_flag=False)
                query,params=cursor.execute.call_args.args
                self.assertEqual(params[-1],event.raw_record)
                self.assertEqual(params[-2].obj,event.values)
                core=list(params[:-2]);core[hash_index]=None
                results.append((query,core))
            self.assertEqual(results[0],results[1],tr)


if __name__=='__main__': unittest.main()

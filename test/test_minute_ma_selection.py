import csv,tempfile,unittest
from pathlib import Path

from src.minute_ma.contracts import Axis
from src.minute_ma.selection import AVAILABLE_AXES,build_selection_rows


class MinuteMaSelectionTest(unittest.TestCase):
    def _file(self,root:Path,name:str,offset:int=0)->Path:
        path=root/name
        with path.open("w",encoding="utf-8",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=["strategy_id","compound_return_pct","trade_count"])
            writer.writeheader()
            for i in range(2400): writer.writerow({"strategy_id":f"DS{i+1:06d}","compound_return_pct":i%20+offset,"trade_count":3})
        return path

    def test_exact_three_axis_2400_contract(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            files={axis:self._file(root,axis.value+".csv",n) for n,axis in enumerate(AVAILABLE_AXES)}
            rows=build_selection_rows(files)
            self.assertEqual({axis:len(value) for axis,value in rows.items()},
                             {Axis.KRX_CONTINUOUS:2400,Axis.KRX_RESET:2400,Axis.INTEGRATED_CONTINUOUS:2400})

    def test_mismatched_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            files={axis:self._file(root,axis.value+".csv") for axis in AVAILABLE_AXES}
            text=files[Axis.KRX_RESET].read_text(encoding="utf-8")
            files[Axis.KRX_RESET].write_text(text.replace("DS000001","OTHER",1),encoding="utf-8")
            with self.assertRaises(ValueError): build_selection_rows(files)


if __name__=="__main__": unittest.main()

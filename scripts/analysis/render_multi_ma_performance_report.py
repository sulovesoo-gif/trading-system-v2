"""외부 서버 없이 열 수 있는 다중 MA 성과 HTML을 생성한다."""
from __future__ import annotations
import html, json
from pathlib import Path

def render_report(*, summaries, trades, output: Path) -> Path:
    rows=list(summaries); payload=json.dumps({"summaries":rows,"trades":list(trades)},default=str,ensure_ascii=False)
    table="".join("<tr>"+"".join(f"<td>{html.escape(str(value))}</td>" for value in row)+"</tr>" for row in rows)
    document=f"""<!doctype html><meta charset=utf-8><title>Trading System V2 다중 MA 성과</title>
<style>body{{font-family:sans-serif;margin:20px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #bbb;padding:6px}}.graph{{height:150px;border:1px solid #888;margin:12px 0;padding:8px}}</style>
<h1>다중 MA 일별 성과</h1><p>거래일별 가격선은 서로 연결하지 않습니다. SESSION_CLOSE는 별도 마커로 표시합니다.</p>
<table><thead><tr><th>전략</th><th>기준</th><th>설정</th><th>가격</th><th>누적 손익</th><th>거래 수</th><th>SIGNAL 청산</th><th>장 종료 청산</th></tr></thead><tbody>{table}</tbody></table>
<h2>거래일별 그래프</h2><div class=graph>타점 1: BUY / SELL / BUY_TO_COVER / SESSION_CLOSE</div><div class=graph>타점 2</div><div class=graph>타점 3</div><div class=graph>누적 분할: 1/3 · 2/3 · 100%</div>
<script>const report={payload}; console.log('multi-ma-report',report);</script>"""
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(document,encoding="utf-8"); return output

if __name__=="__main__":
    raise SystemExit("render_report()를 분석 조회 결과와 함께 호출하세요.")

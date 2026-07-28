# Trading System V2 - API LIST (정리본)

> 기준 문서: 한국투자증권 오픈API 전체문서 2026-07-20
> 시간 기준: KST(Asia/Seoul)

## 1. 확정 API

| No | Collector | 목적 | KIS API 명 | API ID | TR_ID | URL | 주기 | 저장 테이블 |
|---:|---|---|---|---|---|---|---|---|
| 1 | program | 종목별 프로그램매매 | 종목별 프로그램매매추이(체결) | v1_국내주식-044 | FHPPG04650101 | `/uapi/domestic-stock/v1/quotations/program-trade-by-stock` | 1분 | `raw_program` |
| 2 | market_investor | 시장별 투자자 수급 | 시장별 투자자매매동향(시세) | v1_국내주식-074 | FHPTJ04030000 | `/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market` | 1분 | `raw_market_investor` |
| 3 | stock_quote | 종목 현재가·당일 OHLC·누적 거래 | 주식현재가 시세 | v1_국내주식-008 | FHKST01010100 | `/uapi/domestic-stock/v1/quotations/inquire-price` | 1분 | `raw_stock_quote` |
| 4 | stock_execution | 체결시각·체결량·체결강도 | 주식현재가 체결 | v1_국내주식-009 | FHKST01010300 | `/uapi/domestic-stock/v1/quotations/inquire-ccnl` | 1분 | `raw_stock_execution` |
| 5 | stock_minute | 종목 당일 분봉 | 주식당일분봉조회 | v1_국내주식-022 | FHKST03010200 | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | 보완/백필 | `raw_stock_minute` |
| 6 | stock_daily | 종목 일봉 | 국내주식기간별시세(일_주_월_년) | v1_국내주식-016 | FHKST03010100 | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | 장 종료 후 | `raw_stock_daily` |
| 7 | futures_quote | 선물 현재가·OHLC·미결제약정·베이시스 | 선물옵션 시세 | v1_국내선물-006 | FHMIF10000000 | `/uapi/domestic-futureoption/v1/quotations/inquire-price` | 1분 | `raw_futures_quote` |
| 8 | futures_minute | 선물 분봉 및 보완 데이터 | 선물옵션 분봉조회 | v1_국내선물-012 | FHKIF03020200 | `/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice` | 보완/백필 | `raw_futures_minute` |

## 2. 시장별 투자자 API 사용 방식

`시장별 투자자매매동향(시세)` 하나로 현물과 선물 수급을 모두 수집한다.

| 대상 | `fid_input_iscd` | `fid_input_iscd_2` | 내부 market_code 예시 |
|---|---|---|---|
| 코스피 | `KSP` | `0001` | `KOSPI` |
| 코스닥 | `KSQ` | `1001` | `KOSDAQ` |
| 코스피200 선물 | `K2I` | `F001` | `KOSPI200_FUTURES` |
| 코스닥150 선물 | `KQI` | `F002` | `KOSDAQ150_FUTURES` |

현물과 선물 컬럼을 따로 만들지 않는다. 행의 `market_code`로 구분한다.

## 3. 명명 규칙

- API 약어를 그대로 컬럼명으로 쓰지 않는다.
- 프로젝트 컬럼은 일관된 영문 전체 명칭을 사용한다.
- `acc_*` 대신 `accumulated_*`를 사용한다.
- `net_*` 대신 의미가 명확한 `net_buy_*`를 사용한다.
- 가격 필드는 `current_price`, `open_price`, `high_price`, `low_price`, `previous_close_price`로 통일한다.
- 거래량은 `*_volume`, 거래대금은 `*_amount`로 통일한다.
- API 원문 필드는 Collector 매핑에서 보존하고 DB에는 의미 기반 컬럼명으로 저장한다.
- Collector에서 파생 계산은 하지 않는다. 부호 결합도 API 원문 값이 이미 부호를 포함하는지 확인 후 그대로 숫자 변환만 한다.

## 4. 시간 기준

- API가 데이터 기준 날짜·시각을 제공하면 해당 값을 `snapshot_time` 또는 `bar_time`으로 사용한다.
- API가 기준 시각을 제공하지 않으면 실제 API 조회 시각(KST)을 `snapshot_time`으로 사용한다.
- `collected_at`은 Collector가 API 응답을 수신하고 RAW 행으로 변환한 실제 수집 시각(KST)이다.
- `market_investor`, `stock_quote`, `futures_quote`는 API 기준 시각이 없어 KST 실제 조회 시각을 `snapshot_time`으로 사용한다.
- 분봉·일봉 응답 상단 `output1`은 행별 `raw_payload`에 병합하지 않는다. 요청 단위 응답 로그와 공통 메타정보, 행과 직접 대응하지 않는 상단 객체의 저장 방식은 별도 설계 대상이다.
- 실제 운영 응답 확인 결과 `FHPTJ04030000`의 `output`은 투자자 수급 행 목록이다. 각 목록 객체를 `raw_market_investor`의 개별 RAW 행으로 변환한다.
- 실제 운영 응답 확인 결과 `FHPPG04650101`, `FHKST01010300`의 `output`도 목록이다. 각 목록 객체를 각각 `raw_program`, `raw_stock_execution`의 개별 RAW 행으로 변환한다.
- 실제 운영 응답 확인 결과 `FHMIF10000000`은 `output1`, `output2`, `output3` 객체를 반환한다. 월물 시세는 `output1`만 사용하며, `output2`와 `output3`의 지수성 데이터는 `raw_futures_quote` 행으로 저장하지 않는다.

## 5. 테이블별 권장 컬럼

### raw_program

공통 키:

- `snapshot_time`
- `collected_at`
- `data_source`
- `market_code`
- `collect_cycle`
- `stock_code`

API 데이터:

- `current_price` ← `stck_prpr`
- `previous_day_difference` ← `prdy_vrss`
- `previous_day_difference_sign` ← `prdy_vrss_sign`
- `change_rate` ← `prdy_ctrt`
- `accumulated_volume` ← `acml_vol`
- `sell_volume` ← `whol_smtn_seln_vol`
- `buy_volume` ← `whol_smtn_shnu_vol`
- `net_buy_volume` ← `whol_smtn_ntby_qty`
- `sell_amount` ← `whol_smtn_seln_tr_pbmn`
- `buy_amount` ← `whol_smtn_shnu_tr_pbmn`
- `net_buy_amount` ← `whol_smtn_ntby_tr_pbmn`
- `net_buy_volume_change` ← `whol_ntby_vol_icdc`
- `net_buy_amount_change` ← `whol_ntby_tr_pbmn_icdc`

### raw_market_investor

공통 키:

- `snapshot_time`
- `collected_at`
- `data_source`
- `market_code`
- `collect_cycle`

각 투자자별 6개 값을 모두 저장한다.

- `{investor}_sell_volume`
- `{investor}_buy_volume`
- `{investor}_net_buy_volume`
- `{investor}_sell_amount`
- `{investor}_buy_amount`
- `{investor}_net_buy_amount`

투자자 prefix:

- `foreign`
- `individual`
- `institution`
- `financial_investment` (`scrt_*`, 증권)
- `investment_trust`
- `private_fund`
- `bank`
- `insurance`
- `merchant_bank`
- `fund`
- `other_organization`
- `other_corporation`

주의: 기존 `pension`은 API 원문에 독립 필드가 없다. API의 `fund_*`를 임의로 연기금으로 단정하지 말고 `fund`로 저장한다.

### raw_stock_quote

최소 핵심 RAW 필드:

- `snapshot_time`
- `collected_at`
- `data_source`
- `market_code`
- `collect_cycle`
- `stock_code`
- `current_price`
- `previous_day_difference`
- `previous_day_difference_sign`
- `change_rate`
- `open_price`
- `high_price`
- `low_price`
- `base_price`
- `upper_limit_price`
- `lower_limit_price`
- `accumulated_volume`
- `accumulated_amount`
- `weighted_average_price`
- `foreign_net_buy_volume`
- `program_net_buy_volume`
- `vi_classification_code`
- `trading_halt_yn`

API 100% 저장 원칙을 적용하려면 나머지 응답 필드도 동일 테이블에 추가하거나 `raw_payload JSONB`를 함께 저장한다. 프로젝트 초기 구현에서는 명시 컬럼 + `raw_payload` 병행을 권장한다.

### raw_stock_execution

- `snapshot_time` ← 영업일 + `stck_cntg_hour`
- `collected_at`
- `data_source`
- `market_code`
- `collect_cycle`
- `stock_code`
- `current_price` ← `stck_prpr`
- `previous_day_difference` ← `prdy_vrss`
- `previous_day_difference_sign` ← `prdy_vrss_sign`
- `change_rate` ← `prdy_ctrt`
- `execution_volume` ← `cntg_vol`
- `execution_strength` ← `tday_rltv`

체결강도는 별도 순위 API가 아니라 이 종목별 체결 API를 사용한다.

### raw_stock_minute

- `bar_time` ← `stck_bsop_date` + `stck_cntg_hour`
- `collected_at`
- `data_source`
- `market_code`
- `stock_code`
- `open_price`
- `high_price`
- `low_price`
- `close_price` ← `stck_prpr`
- `volume` ← `cntg_vol`
- `accumulated_amount` ← `acml_tr_pbmn`

`close_price`를 사용한다. 분봉 행에서 `current_price`라는 이름은 피한다.

### raw_stock_daily

- `trade_date` ← `stck_bsop_date`
- `collected_at`
- `data_source`
- `market_code`
- `stock_code`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `amount`
- `previous_day_difference`
- `previous_day_difference_sign`
- `adjusted_yn` ← `mod_yn`
- `split_rate` ← `prtt_rate`

### raw_futures_quote

- `snapshot_time`
- `collected_at`
- `data_source`
- `market_code`
- `collect_cycle`
- `futures_code`
- `futures_name`
- `current_price`
- `previous_day_difference`
- `previous_day_difference_sign`
- `previous_close_price`
- `change_rate`
- `open_price`
- `high_price`
- `low_price`
- `upper_limit_price`
- `lower_limit_price`
- `base_price`
- `accumulated_volume`
- `accumulated_amount`
- `open_interest`
- `open_interest_change`
- `basis`
- `theoretical_price`
- `market_basis`
- `expiration_date`
- `days_to_expiration`

### raw_futures_minute

- `bar_time`
- `collected_at`
- `data_source`
- `market_code`
- `futures_code`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `volume`
- `accumulated_amount`

필요한 경우 응답 상단의 `basis`, `open_interest`, `open_interest_change`, `kospi200_index`, `execution_strength`는 분봉 행과 분리하여 quote 수집 보완값으로만 사용한다.

## 6. 기존 파일 변경 판단

### 반드시 변경

- `raw_program`: `price` 계열 Collector 명칭을 DB 명칭과 통일하고 `previous_day_difference_sign` 추가
- `raw_market_flow`: 폐기 또는 `raw_market_investor`로 재설계
- `raw_price`: 현재가와 체결강도를 분리하여 `raw_stock_quote`, `raw_stock_execution`으로 분리
- `raw_futures`: `raw_futures_quote`로 명확히 하고 미결제약정 증감·베이시스 등 API 핵심 RAW 필드 추가
- `API_LIST.md`: API 공식 명칭, API ID, TR_ID, URL, 파라미터를 본 문서 기준으로 교체

### 새로 추가

- `raw_stock_minute`
- `raw_stock_daily`
- `raw_futures_minute`
- 각 API별 Collector 1개

## 7. 수집 우선순위

1. `program`
2. `market_investor` (KOSPI, KOSDAQ, KOSPI200_FUTURES)
3. `stock_quote`
4. `futures_quote`
5. `stock_execution`
6. `stock_minute` 백필
7. `stock_daily` 장 종료 후
8. `futures_minute` 백필

## 8. 선물 스모크 테스트 기준

- 선물 테스트 종목은 한국투자증권 공식 지수선물 마스터 `fo_idx_code_mts.mst` 기준 `A01609`을 사용한다.
- 확인된 종목 정보는 표준코드 `KR4A01690002`, 종목명 `F 202609`, 기초자산 `KOSPI200`, 만기월 2026년 9월이다.
- `101X9000`, `101609`, `KR4101690003`은 공식 지수선물 마스터에서 확인되지 않아 사용하지 않는다.
- `FHMIF10000000.output1`에는 선물 단축코드가 없으므로, Collector 요청 인자 `A01609`을 `raw_futures_quote.futures_code`로 저장한다. `raw_payload`에는 실제 `output1` 원본 객체만 저장한다.
- `FHKIF03020200`은 `A01609`에 대해 최신순 `output2` 102행을 반환한다. 각 행의 `stck_bsop_date`와 `stck_cntg_hour`를 결합해 `raw_futures_minute.bar_time`으로 저장한다.

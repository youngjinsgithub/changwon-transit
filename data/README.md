# 데이터 수집 가이드

창원 대중교통 분석에 필요한 데이터를 어디서, 어떻게 받는지 정리한 문서.

## 폴더 규칙

| 폴더 | 용도 | git 커밋? |
|------|------|----------|
| `data/raw/` | 원본 파일 (절대 수정 X) | ❌ (.gitignore) |
| `data/processed/` | 정제·변환된 CSV/Parquet | ❌ (대용량) |
| `data/geo/` | GeoJSON, Shapefile 등 공간 데이터 | ❌ |

`raw/` 안에 소스별 하위 폴더를 두면 깔끔합니다:
```
data/raw/
├── stops/        # 국토부 정류장 CSV
├── stcis/        # STCIS 승하차 CSV (월별로 여러 개)
├── tago/         # TAGO API 응답 캐시 (자동 생성)
└── cache/        # HTTP 캐시 (자동 생성)
```

---

## 받아야 할 데이터 (4종 + 1 옵션)

우선순위 순.

### 1. 국토교통부 전국 버스정류장 위치 CSV (보강용, 옵션)

> **TAGO 정류소정보(15098534) API 로 정류장 마스터를 받을 수 있음을 확인** (2026-05-13).
> 본 CSV 는 **TAGO API 가 누락한 정류장 보완용 백업**으로만 필요. 처음엔 받지 않아도 됨.

- **링크**: https://www.data.go.kr/data/15067528/fileData.do
- **인증**: 불필요 (로그인 없이 다운로드 가능)
- **다운로드**: 페이지 우측 **내려받기** → `data/raw/stops/` 저장
- **용도**: TAGO 정류소가 누락된 경우 좌표 보강 (Step 3 매칭 시 사용)
- **최신 갱신**: 2025-10-31 (연 1회 갱신)

**TAGO vs 국토부 CSV 비교 결과 (2026-05-14, [scripts/compare_tago_vs_molit.py](../scripts/compare_tago_vs_molit.py)):**

| 항목 | TAGO (DB `stops`) | 국토부 CSV (`도시코드 == 38010`) |
|---|---|---|
| 창원 정류장 수 | 2,751 | 3,518 |
| 공통 (`stop_id` 일치) | 2,741 (99.6%) | 2,741 (77.9%) |
| 한쪽에만 있음 | 10 (TAGO 최신 신규) | **777** (장유·외곽 다수) |
| 좌표 1m 이내 일치 | 2,628 / 2,741 (96%) | — |
| 50m 이상 차이 | 11건 | — |
| 이름이 다른 행 | 24건 (TAGO 가 최신 명칭) | — |

→ **결론:** TAGO 가 메인 마스터로 충분하지만, **777 개 누락 정류장**(주로 장유·동남 외곽)은 국토부 CSV 로만 확보 가능. STCIS 정류장 매칭에서 미스가 나면 그때 보강하면 됨.

---

### 2. STCIS 정류장별 시간대별 승하차 CSV ⭐ EDA 핵심

- **링크**: https://www.stcis.go.kr/wps/main.do
- **인증**: 회원가입 필요할 수 있음 (사이트 정책 변경 가능)
- **다운로드**:
  1. 위 사이트 접속·로그인
  2. 좌측 메뉴 **지표조회 → 노선·정류장 지표 → 정류장별 이용량** (또는 유사 메뉴 명칭)
  3. 조회 조건:
     - 지역: **경상남도 창원시**
     - 기간: 원하는 월 (예: 2024-01 ~ 2024-12)
     - 시간대별 출력: **Y**
  4. 조회 후 **CSV/엑셀 다운로드**
  5. `data/raw/stcis/` 에 월별 파일로 저장 (예: `stcis_2024_01.csv`)
- **용도**: Step 5 ETL → Step 6 EDA 의 핵심 데이터
- **파일 포맷**: 와이드 — 정류장 × 일자 × {00승, 00하, ..., 23승, 23하}

---

### 3. TAGO API 키 ⭐ 핵심

> 인증키 **1개로 정류소·노선 양쪽 API 호출 가능** 확인 (2026-05-13). 사용자가 정류소정보를 신청한 키로 노선 API 도 정상 응답.

- **신청할 데이터셋**:
  - 버스정류소정보(15098534): https://www.data.go.kr/data/15098534/openapi.do
  - 버스노선정보(15098529): https://www.data.go.kr/data/15098529/openapi.do
- **신청 절차**:
  1. https://www.data.go.kr 회원가입·로그인 (PC 만 지원)
  2. 위 페이지에서 우측 **활용신청** → **개발계정** 선택
  3. 활용목적 입력: `2026 경상남도 빅데이터 활용 공모전 출품작 — 창원 대중교통 데이터 EDA 및 공간 분석`
  4. 자동승인 → 마이페이지에서 **인증키** 복사
  5. `.env` 의 `TAGO_API_KEY=` 에 붙여넣기
- **활용 가능한 endpoint** (창원 도시코드 `38010`):
  - `getSttnNoList?cityCode=38010` — **창원 전체 정류소 목록** (nodeid, nodeno, nodenm, gpslati, gpslong)
  - `getRouteNoList?cityCode=38010` — 창원 전체 노선 목록 (routeid, routeno, routetp, startnodenm, endnodenm)
  - `getRouteAcctoThrghSttnList?cityCode=38010&routeId=…` — 노선별 경유 정류소
  - `getCrdntPrxmtSttnList?gpsLati=…&gpsLong=…` — 좌표 반경 500m 근접 정류소
  - `getCtyCodeList` — 전국 도시코드 마스터 (참고용)
- **DB 적재 매핑**: 정류소→`stops`, 노선→`routes`, 경유→`route_stops`

---

### 4. ~~창원 BIS API 키~~ **(2027-01-01 종료 예정, 신청 불가)**

> ⚠️ **사용 금지** — 창원시 공지(2026-05-14 확인):
> > "2027년 1월 1일부로 창원시 API 서비스가 종료 예정이오니, 신규가입자 및 기존가입자께서는 국토교통부 TAGO API로 이용 및 전환하시기 바랍니다.
> > (※ 현재 창원시 신규 신청자 API 인증키 승인 불가)"
>
> → **신규 키 발급 자체가 막혔고**, 1년 7개월 후 종료. 창원시가 TAGO 로 통합 이전 중. 이 경로는 더 이상 시도하지 말 것.

**대체: 창원 BIS 가 제공하던 4개 서비스 전부 TAGO 로 이전됨.**

| 구 창원 BIS 서비스 | 대체 TAGO 데이터셋 | 우리 신청 상태 |
|---|---|---|
| 정류소목록/정보 | 버스정류소정보 (15098534) | ✅ 신청·사용 중 |
| 버스노선목록/정보, 경유정류소 | 버스노선정보 (15098529) | ✅ 신청·사용 중 |
| 노선버스 위치, 노선별 버스위치 | 버스위치정보 (15098533) | ❌ 미신청 (실시간, EDA 단계엔 불필요) |
| 버스 도착정보 | 버스도착정보 (15098530) | ❌ 미신청 (실시간, EDA 단계엔 불필요) |

링크:
- 버스정류소정보: https://www.data.go.kr/data/15098534/openapi.do
- 버스노선정보: https://www.data.go.kr/data/15098529/openapi.do
- 버스위치정보: https://www.data.go.kr/data/15098533/openapi.do
- 버스도착정보: https://www.data.go.kr/data/15098530/openapi.do

코드: [src/api/changwon_bis.py](../src/api/changwon_bis.py) 는 deprecated 처리 (호출 시 경고). 환경변수 `CHANGWON_BIS_API_KEY` 도 사용하지 않음.

---

### 5. (옵션) 창원시 행정동 경계 GeoJSON

Step 7 공간 분석(행정동별 정류장 수·이용량 등)에서 필요. 지금 안 받아도 됨.

- 행정안전부 도로명주소 (https://www.juso.go.kr) — 공식 경계
- 통계지리정보서비스 SGIS (https://sgis.kostat.go.kr) — 행정구역 통계
- GitHub 커뮤니티 GeoJSON: 예) `southkorea/southkorea-maps` 등
- 저장 위치: `data/geo/`

---

## 추천 수령 순서

| 순서 | 작업 | 상태 |
|------|------|------|
| 1 | data.go.kr 가입 + TAGO **정류소정보·노선정보** 활용신청 (3번) | ✅ 완료 (2026-05-13) |
| 2 | STCIS 가입 + 창원 시간대별 승하차 CSV 다운로드 (2번) | ⏳ 대기 |
| 3 | (옵션) 국토부 정류장 CSV (1번), 행정동 경계 (5번) | 후순위 |
| ~~–~~ | ~~창원 BIS (4번)~~ | ❌ **신청 불가 · 종료 예정** |

**2번까지만 받으면 Step 2~7 전체가 돌아갑니다.** TAGO 가 메인 마스터.

### 데이터 매핑 (어떤 데이터가 어떤 DB 테이블로 가는지)

| 데이터 | 적재 테이블 |
|--------|------------|
| 3번 TAGO 정류소 API ⭐ | `stops` (정류장 마스터) |
| 3번 TAGO 노선 API ⭐ | `routes` + `route_stops` |
| 2번 STCIS 승하차 CSV | `boarding_data` (시간대별 이용량) |
| 자동 계산 | `stop_distances` (정류장 쌍 거리, Step 4) |
| 1번 국토부 CSV (옵션) | `stops` 보강 (좌표 누락 보완 — TAGO 누락 777건 대비) |
| 5번 행정동 GeoJSON (옵션) | `districts` (공간 조인용) |

---

## 받은 다음 할 일

1. 파일을 `data/raw/<source>/` 에 두기
2. API 키는 `.env` 에 입력 (`.env.example` 참고)
3. Claude 한테 "데이터 받았어, 노트북 진행해줘" 라고 알려주기 → `notebooks/01_data_collection.ipynb` 에서 로딩 + DB 적재 진행

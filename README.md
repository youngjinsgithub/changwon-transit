# 창원 대중교통 데이터 분석

창원시 대중교통 공공데이터(TAGO 정류장·노선, STCIS 승하차, 행안부 인구통계, 행정동 GeoJSON)를
수집·정제하여 **PostgreSQL + PostGIS** 위에서 EDA·공간 분석을 수행하는 프로젝트.

**2026 경상남도 빅데이터 활용 공모전 출품작.**

📍 **메인 산출물**: [data/processed/maps/district_per_capita.html](data/processed/maps/district_per_capita.html)
— 행정동 인구·1인당 승차·정류장 밀도·고령 비율 통합 분석 지도 (2,751 정류장 시간대 그래프 포함)

---

## 🚀 팀원 빠른 시작 (재현 절차)

> 핵심 데이터가 repo 에 다 포함되어 있어 **STCIS 7시간 수집 작업이 불필요**합니다.
> 클론 후 ~30분 내 동일한 지도 재생성 가능.

### 1. 사전 요구사항
- **Docker Desktop** 실행 중
- **Python 3.11** (또는 `conda env create transit` 환경)
- TAGO 인증키 ([data.go.kr/data/15098534](https://www.data.go.kr/data/15098534/openapi.do)·[15098529](https://www.data.go.kr/data/15098529/openapi.do) 활용신청, 자동 승인)

### 2. 클론·환경
```bash
git clone https://github.com/youngjinsgithub/changwon-transit.git
cd changwon-transit
cp .env.example .env
# .env 의 TAGO_API_KEY=  에 본인 키 입력

pip install -r requirements.txt
```

### 3. 압축 데이터 풀기
```bash
# Windows (PowerShell)
conda run -n transit python -c "import gzip, shutil; \
  gzip.open('data/processed/stcis_boarding_long.csv.gz','rb').read() and \
  shutil.copyfileobj(gzip.open('data/processed/stcis_boarding_long.csv.gz','rb'), \
  open('data/processed/stcis_boarding_long.csv','wb'))"

# Linux/Mac
gunzip data/processed/stcis_boarding_long.csv.gz
```

### 4. 데이터 적재 (각 1~5분)
```bash
python scripts/init_db.py                # ① Docker + 스키마
python scripts/collect_tago.py           # ② TAGO 정류장·노선 수집 + 적재
python scripts/load_districts.py         # ③ 행정동 GeoJSON 적재
python scripts/stcis_load_mapping.py     # ④ STCIS↔TAGO 매핑 (CSV → DB)
python scripts/stcis_load_db.py          # ⑤ STCIS 승하차 적재 + 집계 (7시간 SKIP!)
python scripts/load_population.py        # ⑥ 행안부 인구 적재
```

### 5. 지도 생성 (~10분, 한 번만)
```bash
python scripts/visualize_district_per_capita.py
```

→ [`data/processed/maps/district_per_capita.html`](data/processed/maps/district_per_capita.html) (19MB) 생성·갱신.
HTML 그대로 브라우저로 열어도 됨 (이미 repo 에 포함).

---

## 📁 폴더 구조

```
.
├── README.md / docker-compose.yml / requirements.txt / .env.example
├── sql/01_schema.sql                 # PostGIS 확장 + 8개 테이블
├── src/
│   ├── api/{tago,stcis_scraper,base}.py   # API 클라이언트
│   ├── db/{connection,upsert}.py          # DB 헬퍼
│   ├── geo/distances.py                   # 정류장 거리 (Haversine)
│   └── utils/{logger,encoding}.py
├── scripts/                          # 데이터 파이프라인·시각화 entry point (~22개)
│   ├── init_db.py / test_connection.py
│   ├── collect_tago.py / load_districts.py / load_population.py
│   ├── stcis_{build_mapping,fetch_boarding,load_mapping,load_db}.py
│   ├── select_priority_routes.py / generate_all_stops_by_name.py
│   ├── compare_{tago_vs_molit,population_sources}.py
│   ├── stcis_inspect.py / stcis_*probe*.py (개발 시 사용)
│   └── visualize_{map,priority_routes,district_per_capita}.py
├── data/
│   ├── raw/{stcis,stops,population,cache}/   # 수집 원본 + 매핑·가이드
│   ├── processed/{maps,*.csv,*.csv.gz}/      # 산출물·지도
│   └── geo/HangJeongDong_*.geojson          # 행정동 경계
├── config/api_keys.yaml.example
├── notebooks/   (placeholder — 분석은 scripts/ 에서)
└── tests/
```

## 📊 데이터 소스

| 소스 | 단위 | 비고 |
|---|---|---|
| **TAGO API** ⭐ | 정류장·노선 마스터 | data.go.kr — 자동, 5분 |
| **STCIS** ⭐ | 정류장 시간대별 승하차 | HTTP 스크래핑 (`indicatorAjax.do`), 본 수집 7시간 |
| **행안부 주민등록 인구** | 행정동 성별·연령별 | jumin.mois.go.kr 수동 다운, repo 포함 |
| **행정안전부 GeoJSON** | 행정동 경계 폴리곤 | GitHub `vuski/admdongkor`, repo 포함 |
| (옵션) 국토부 MOLIT CSV | 정류장 좌표 보강용 | TAGO 보완용, repo 포함 |
| ~~창원 BIS~~ | ~~정류장·노선·실시간~~ | **2027-01-01 종료. TAGO 로 통합** |

## 🛠 기술 스택

- **인프라**: Docker, PostgreSQL 16 + PostGIS 3.4, pgAdmin 4
- **Python 3.11** (conda env `transit` 권장)
- **핵심 라이브러리**: pandas, SQLAlchemy + psycopg2, geopandas / shapely / pyproj, folium, branca, beautifulsoup4, requests

---

## 🧮 분석 단위·매핑

### 정류장 단위 (TAGO stop_id)
- 2,751 정류장 (창원 전체)
- 12 우선순위 노선 938 정류장 (분석 표적)
- STCIS sttn 2,729 매칭 (`stcis_stop_mapping` 테이블)

### 행정동 단위 (55개)
- HangJeongDong geojson 기준 (행정안전부 행정동, 2026-04 기준)
- 인구·면적·정류장 수·승하차 합산 가능

### 더블카운팅 자동 보정
- `boarding_data.boarding` = `SUM(매칭 STCIS sttn) ÷ n_tago_in_grp`
- 상행/하행 페어 (n=2) → 각자 평균값 공유. 합산 시 STCIS 실제 총량과 일치

---

## 🔁 멱등 재실행

모든 ETL 은 `ON CONFLICT DO UPDATE` (UPSERT) 라 같은 스크립트 여러 번 돌려도 결과 동일.

```bash
docker compose down          # 컨테이너만 정지 (DB 유지)
docker compose up -d         # 재기동
docker compose down -v       # DB 까지 삭제 — 주의
```

## 📐 규칙

- **좌표계**: WGS84 (EPSG:4326). 거리 계산 시 `::geography` 캐스팅
- **시간대**: KST (Asia/Seoul) 일관
- **API 키**: `.env` (gitignored). `.env.example` 참조
- **코드 식별자**: 영문 `snake_case` / **주석·DB COMMENT**: 한국어 OK

## 🩹 트러블슈팅

| 증상 | 해결 |
|---|---|
| `docker compose` 명령 없음 | Docker Desktop 실행 확인 |
| `POSTGRES_PASSWORD` 오류 | `.env` 가 프로젝트 루트에 있는지 확인 |
| 포트 5432/8080 충돌 | `.env` 의 `POSTGRES_PORT`/`PGADMIN_PORT` 변경 |
| pgAdmin 에서 `localhost` 연결 실패 | 호스트를 `transit_postgis` 또는 호스트 머신 IP 로 |
| `ModuleNotFoundError: No module named 'branca'` | conda transit 환경 활성화 (`conda activate transit`) |
| 19MB 지도 HTML 로딩 느림 | 정상 — 첫 로딩 5~15초 |

---

## 📜 라이선스·출처

- TAGO API: data.go.kr 공공데이터
- STCIS: 한국교통안전공단 stcis.go.kr
- 인구: 행정안전부 jumin.mois.go.kr
- 행정동 경계: github.com/vuski/admdongkor (CC-BY)

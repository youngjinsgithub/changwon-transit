# 창원 대중교통 데이터 분석

창원시 대중교통 공공데이터(STCIS 승하차, 창원 BIS, 국토부 TAGO 등)를 수집·정제하여
PostgreSQL + PostGIS 위에서 EDA·공간 분석을 수행하는 프로젝트.

> 주제는 미정. EDA로 데이터가 보여주는 패턴/이상치/기회를 먼저 발견한 뒤,
> 그 위에서 분석 주제를 도출한다.

---

## 폴더 구조

```
.
├── docker-compose.yml      # PostGIS + pgAdmin
├── .env.example            # 환경변수 템플릿
├── requirements.txt        # Python 3.11 의존성 (== 버전 핀)
├── sql/
│   └── 01_schema.sql       # PostGIS 확장 + 6개 테이블 + 인덱스 + COMMENT
├── src/
│   ├── api/                # 외부 API 클라이언트 (Step 2)
│   ├── etl/                # 와이드→롱 변환 등 (Step 2/5)
│   ├── geo/                # 정류장 매칭·거리 (Step 3/4)
│   ├── db/connection.py    # SQLAlchemy 엔진 헬퍼
│   └── utils/
├── scripts/
│   ├── init_db.py          # docker up + schema 멱등 적용
│   └── test_connection.py  # PostGIS 동작 검증
├── notebooks/              # 단계별 분석 노트북 (placeholder)
├── config/                 # API 키 YAML 템플릿
├── data/{raw,processed,geo}/
└── tests/
```

## 데이터 소스 (수집 우선순위)

1. **STCIS** (stcis.go.kr) — 정류장별 시간대별 승하차 (와이드 CSV)
2. **국토부 전국 버스정류장 위치정보** (CSV) — 좌표
3. **창원 BIS** (openapi.changwon.go.kr) — 정류장/노선/실시간
4. **국토부 TAGO API** (data.go.kr) — 전국 노선·정류장

## 기술 스택

- **인프라**: Docker, PostgreSQL 16 + PostGIS 3.4, pgAdmin 4
- **Python 3.11** + venv
- **핵심 라이브러리**: pandas, SQLAlchemy + psycopg2, geopandas / shapely / geopy / pyproj, networkx, folium / plotly / contextily, fuzzywuzzy

---

## 셋업 가이드 (Step 1)

### 1. 사전 요구사항

- Docker Desktop (실행 중)
- Python 3.11
- (Windows) PowerShell

### 2. 저장소 클론 & 진입

```powershell
cd c:\Users\user\ai동아리
```

### 3. 환경변수 파일 생성

```powershell
Copy-Item .env.example .env
# .env 를 열어 POSTGRES_PASSWORD 등 필수 값을 채울 것
notepad .env
```

### 4. Python 가상환경 & 의존성

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5. DB 초기화 (Docker 기동 + Schema 적용)

```powershell
python scripts\init_db.py
```

기대 출력:
```
[1/4] docker compose up -d ...
      → 성공: docker compose up -d
[2/4] PostGIS readiness 대기 (최대 60s) ...
      → ready
[3/4] schema 적용: sql\01_schema.sql
      → 적용 완료
[4/4] 버전 확인
      PostgreSQL : 16.x
      PostGIS    : 3.4 USE_GEOS=1 USE_PROJ=1 USE_STATS=1
```

### 6. 연결·공간 기능 검증

```powershell
python scripts\test_connection.py
```

기대 출력:
```
[1/3] PostGIS 버전 확인
[2/3] POINT 삽입·조회 테스트
      삽입: 창원시청
      WKT : POINT(128.6811 35.228)
      SRID: 4326
[3/3] 거리 계산 테스트 (geography 캐스팅)
      창원시청 ↔ 마산합포구청 ≈ 11,xxx.x m  (~11 km)

[OK] PostGIS 정상 작동 확인 완료.
```

### 7. pgAdmin 으로 스키마 확인

브라우저에서 `http://localhost:8080` 접속.

- 로그인: `.env` 의 `PGADMIN_EMAIL` / `PGADMIN_PASSWORD`
- 서버 추가 시 호스트는 **컨테이너 이름 `transit_postgis`** (pgAdmin 컨테이너 내부에서 본 호스트)
- 6개 테이블이 존재해야 함: `routes`, `stops`, `route_stops`, `stop_distances`, `boarding_data`, `districts`

---

## 멱등 재실행

`scripts/init_db.py` 는 몇 번을 실행해도 동일 결과를 보장한다.
- `CREATE EXTENSION IF NOT EXISTS`
- `CREATE TABLE IF NOT EXISTS`
- `CREATE INDEX IF NOT EXISTS`
- `COMMENT ON` 은 본래 멱등

스키마를 변경했다면 `01_schema.sql` 을 수정하고 다시 `init_db.py` 실행하면 된다
(단, 컬럼 타입 변경 등 비호환 변경은 별도 마이그레이션 필요).

## 컨테이너 정지 / 재기동

```powershell
docker compose down          # 컨테이너 정지·제거 (볼륨은 유지)
docker compose up -d         # 재기동
docker compose down -v       # 데이터 볼륨까지 모두 삭제 (주의)
```

## 다음 단계 (Step 2~)

| Step | 내용                              | 산출물 |
|------|-----------------------------------|--------|
| 2    | 데이터 수집 모듈                  | `src/api/*.py`, `src/etl/stops_loader.py` |
| 3    | 정류장 매칭 (좌표 + 이름)         | `src/geo/matching.py` |
| 4    | 정류장 간 거리 계산               | `src/geo/distances.py` + `stop_distances` 적재 |
| 5    | STCIS 와이드→롱 ETL              | `src/etl/stcis_loader.py` + `boarding_data` 적재 |
| 6    | EDA                                | `notebooks/05_eda.ipynb` |
| 7    | 공간 분석 (PostGIS)               | `notebooks/06_spatial_analysis.ipynb` |
| 8    | 시각화 대시보드 (선택)            | folium / plotly |

## 규칙

- 코드 식별자: 영문 `snake_case`
- 주석·문서·DB `COMMENT ON`: 한국어 OK
- 좌표계: 모든 데이터 **WGS84 (EPSG:4326)**. 거리 계산 시 `::geography` 캐스팅
- 시간대: **KST (Asia/Seoul)** 일관
- API 키: `.env` 또는 `config/api_keys.yaml` (둘 다 .gitignore)
- ETL: 모두 UPSERT (`ON CONFLICT`) 로 멱등성 보장
- 인코딩: CSV 로딩 시 `chardet` 자동 감지

## 트러블슈팅

- **`docker compose` 명령을 찾을 수 없음**: Docker Desktop 이 실행 중인지 확인.
- **`POSTGRES_PASSWORD` 미설정 오류**: `.env` 파일이 프로젝트 루트에 있는지 확인.
- **포트 5432/8080 충돌**: `.env` 의 `POSTGRES_PORT`/`PGADMIN_PORT` 변경.
- **`psycopg2` 설치 실패**: `psycopg2-binary` 가 명시되어 있어 일반적으로 빌드 불필요. PATH 와 wheel 가용성 확인.
- **pgAdmin 에서 `localhost` 연결 실패**: pgAdmin 컨테이너 내부 기준이므로 호스트는 `transit_postgis` 또는 호스트 머신 IP 를 사용.

# 창원시 버스 노선 개편 우선순위 분석

**2026 경상남도 빅데이터 활용 공모전 출품작**

창원시 대중교통 공공데이터를 수집·분석하여 **버스 노선 개편 우선순위 상위 10개 노선**을 선정하고,
팀원이 각 노선을 분담하여 개편안을 도출하는 프로젝트입니다.

---

## 👥 팀 분업 구조

시나리오 A 알고리즘으로 창원시 전체 167개 노선을 점수화한 결과, **상위 10개 노선**을 3인이 분담합니다.

| 순위 | 노선 | 종합점수 | 승차량 | 소외도 | 담당 |
|------|------|---------|--------|--------|------|
| 1위 | **BRT일반(5000)** | 0.7109 | 1,582,703 | 0.120 | 윤호 |
| 2위 | **100번** | 0.6884 | 1,481,301 | 0.129 | 윤호 |
| 3위 | **122번** | 0.6685 | 1,379,749 | 0.135 | 윤호 |
| 4위 | **102번** | 0.6663 | 1,429,359 | 0.124 | 가희 |
| 5위 | **103번** | 0.6631 | 1,450,408 | 0.118 | 가희 |
| 6위 | **109번** | 0.6354 | 1,387,702 | 0.121 | 가희 |
| 7위 | **64-1번** | 0.6287 | 1,090,412 | 0.155 | 가희 |
| 8위 | **113번** | 0.6159 | 1,261,912 | 0.106 | 태현 |
| 9위 | **111번** | 0.6147 | 1,198,776 | 0.115 | 태현 |
| 10위 | **41번** | 0.6094 | 1,034,308 | 0.154 | 태현 |

### 📂 담당자별 분석 폴더

| 담당 | 폴더 | 담당 노선 |
|------|------|----------|
| 윤호 | [`analysis/yunho_routes/`](analysis/yunho_routes/README.md) | BRT일반(5000)·100·122번 |
| 가희 | [`analysis/gahee_routes/`](analysis/gahee_routes/README.md) | 102·103·109·64-1번 |
| 태현 | [`analysis/taehyun_routes/`](analysis/taehyun_routes/README.md) | 113·111·41번 |

---

## 📊 시나리오 A — 우선순위 산정 알고리즘

창원시 167개 노선을 5개 지표로 Min-Max 정규화 후 가중치 합산.

```
종합점수 = 교통량      × 0.30   (승차량으로 대체 — 대리지표)
         + 승차 수요   × 0.30
         + 배차간격    × 0.20   (경쟁노선 적은 지역일수록 높은 점수)
         + 소외도      × 0.10   (65세 이상 인구 비율)
         + 인구 역산   × 0.10   (인구 많은 곳은 낮은 점수 — 이미 혜택 받는 지역)
```

> **선정 기준**: 승객 수요가 크고, 배차간격이 길고, 고령자 비율이 높은 노선일수록 개편 우선순위가 높습니다.

### 관련 스크립트

| 파일 | 역할 |
|------|------|
| [`scripts/scenario_A_map.py`](scripts/scenario_A_map.py) | 167개 노선 전체 점수 계산 + TOP 10 지도 생성 |
| [`scripts/scenario_A_selected.py`](scripts/scenario_A_selected.py) | 선택 노선만 체크박스 지도 생성 |
| [`data/processed/scenario_A_result.csv`](data/processed/scenario_A_result.csv) | 전체 노선 점수 결과 (167개) |

### 지도 산출물

| 지도 | 내용 |
|------|------|
| [`data/processed/maps/scenario_A_top3.html`](data/processed/maps/scenario_A_top3.html) | 윤호 담당 노선 (BRT·100·122) |
| [`analysis/gahee_routes/routes_map.html`](analysis/gahee_routes/routes_map.html) | 가희 담당 노선 (102·103·109·64-1) |
| [`data/processed/maps/scenario_A_map.html`](data/processed/maps/scenario_A_map.html) | TOP 10 전체 노선 지도 |

---

## 🚀 팀원 빠른 시작

> 핵심 데이터가 repo에 포함되어 있어 STCIS 7시간 수집 없이 바로 재현 가능합니다.

### 1. 사전 요구사항
- **Docker Desktop** 실행 중
- **Python 3.11**
- TAGO 인증키 ([data.go.kr](https://www.data.go.kr/data/15098534/openapi.do) 활용신청, 자동 승인)

### 2. 클론 및 환경 설정
```bash
git clone https://github.com/youngjinsgithub/changwon-transit.git
cd changwon-transit
cp .env.example .env
# .env 파일에서 TAGO_API_KEY= 에 본인 키 입력

pip install -r requirements.txt
```

### 3. DB 초기화 및 데이터 적재
```bash
python scripts/init_db.py          # Docker + 스키마 생성
python scripts/collect_tago.py     # TAGO 정류장·노선 수집
python scripts/load_districts.py   # 행정동 GeoJSON 적재
python scripts/stcis_load_mapping.py  # STCIS↔TAGO 매핑
python scripts/stcis_load_db.py    # 승하차 데이터 적재 (repo 포함 CSV 사용)
python scripts/load_population.py  # 인구 데이터 적재
```

### 4. 시나리오 A 분석 실행
```bash
python scripts/scenario_A_map.py   # 점수 계산 + TOP10 지도 생성
```

### 5. 담당 노선 지도 생성
```bash
# scenario_A_selected.py 상단의 TARGET_BUS_NOS를 담당 노선으로 수정 후 실행
python scripts/scenario_A_selected.py
```

---

## 📁 폴더 구조

```
changwon-transit/
├── analysis/                         # 팀원별 노선 분석
│   ├── yunho_routes/                 # 윤호님: BRT(5000)·100·122번
│   ├── gahee_routes/                 # 가희님: 102·103·109·64-1번
│   └── taehyun_routes/              # 태현님: 113·111·41번
├── scripts/                          # 데이터 파이프라인·분석 스크립트
│   ├── init_db.py                    # Docker + DB 스키마 초기화
│   ├── collect_tago.py               # TAGO API 수집
│   ├── load_districts.py             # 행정동 GeoJSON 적재
│   ├── load_population.py            # 인구 데이터 적재
│   ├── stcis_*.py                    # STCIS 승하차 수집·적재
│   ├── scenario_A_map.py             # 시나리오 A 메인 분석
│   └── scenario_A_selected.py        # 선택 노선 지도 생성
├── data/
│   ├── processed/
│   │   ├── scenario_A_result.csv     # 전체 167개 노선 점수
│   │   └── maps/                     # 생성된 지도 HTML
│   └── raw/                          # 수집 원본 데이터
├── sql/01_schema.sql                 # PostGIS 스키마
├── src/                              # API 클라이언트·DB 헬퍼
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## 📊 데이터 소스

| 소스 | 내용 | 수집 방법 |
|------|------|----------|
| **TAGO API** | 정류장·노선 마스터 (2,751 정류장, 167개 노선) | data.go.kr 자동 수집 |
| **STCIS** | 정류장 시간대별 승하차 (~150만 행) | HTTP 스크래핑, repo에 포함 |
| **행안부 인구** | 행정동별 성별·연령별 인구 | jumin.mois.go.kr, repo에 포함 |
| **행정안전부 GeoJSON** | 창원시 55개 행정동 경계 | vuski/admdongkor, repo에 포함 |

## 🛠 기술 스택

- **DB**: PostgreSQL 16 + PostGIS 3.4 (Docker)
- **언어**: Python 3.11
- **주요 라이브러리**: pandas, SQLAlchemy, psycopg2, folium, geopandas, numpy

---

## 🩹 트러블슈팅

| 증상 | 해결 |
|------|------|
| `docker compose` 명령 없음 | Docker Desktop 실행 확인 |
| `POSTGRES_PASSWORD` 오류 | `.env` 파일이 프로젝트 루트에 있는지 확인 |
| 포트 5432 충돌 | `.env`의 `POSTGRES_PORT` 변경 |
| 지도 HTML 로딩 느림 | 정상 — 첫 로딩 5~15초 소요 |
| Windows 한글 인코딩 오류 | `$env:PYTHONUTF8 = "1"` 설정 후 실행 |

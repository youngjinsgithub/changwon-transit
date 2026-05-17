# 창원 행정동별 인구 데이터 수집 가이드

`districts` 테이블에 인구 정보를 통합하기 위한 3개 소스 비교용.

다운로드해서 이 폴더에 저장 → `python scripts/compare_population_sources.py` → 비교 리포트.

---

## 📂 저장 규칙

이 폴더 (`data/raw/population/`) 에 다음 이름으로 저장:

| 소스 | 파일명 |
|---|---|
| 행안부 (MOIS) | `mois_<YYYYMM>.csv` (예: `mois_202604.csv`) |
| SGIS API | `sgis_<YYYYMM>.csv` |
| KOSIS / 공공데이터 | `kosis_<YYYYMM>.csv` 또는 `bigdata_<YYYYMM>.csv` |

→ 비교 스크립트가 파일명 prefix 로 소스 식별.

---

## 소스 A: 행정안전부 주민등록 인구통계 ⭐ 권장

가장 권위 있는 출처. **수동 다운로드 1분**.

1. **사이트 접속**: https://jumin.mois.go.kr/ageStatMonth.do
   (행정동별 연령별 인구현황 페이지)
2. **조회 조건 설정**:
   - 조회기간: 최신 월 (예: 2026년 04월)
   - 행정구역: **경상남도 → 창원시 → 전체 선택**
   - 연령단위: **1세** 또는 **5세** (분석 풍부도 ↑)
   - 등록구분: 거주자
3. **검색** → 결과 표시 후 우상단 **CSV / 엑셀 다운로드** 클릭
4. 받은 파일을 이 폴더에 `mois_<YYYYMM>.csv` 로 저장

**대안 (공공데이터포털, 자동 가능):**
- https://www.data.go.kr/data/15097972/fileData.do (행정동 성별 연령별)
- 페이지 가서 직접 "내려받기" 클릭 → 같은 형식 CSV

---

## 소스 B: SGIS 오픈API (자동)

통계청 SGIS — API 호출로 자동 수집.

### 키 신청 (1회)

1. https://sgis.kostat.go.kr/developer/html/newOpenApi/api/dataApi/addressList.html 접속
2. SGIS 회원가입 + 인증키 발급 신청
3. 발급된 키를 `.env` 의 `SGIS_API_KEY=` 에 입력

### 실행

```powershell
python scripts/load_population_sgis.py
```

→ `data/raw/population/sgis_<YYYYMM>.csv` 자동 생성

---

## 소스 C: KOSIS API 또는 창원시 빅데이터 (자동/수동)

**옵션 1: 창원시 빅데이터 포털 (수동, 1분)**
- https://bigdata.changwon.go.kr/portal/dataset/datasetList.do
- 키워드 "인구" 검색 → 행정동별 인구 데이터셋 다운로드
- `data/raw/population/bigdata_<YYYYMM>.csv` 로 저장

**옵션 2: 공공데이터포털 행안부 OpenAPI (자동, 키 필요)**
- https://www.data.go.kr/data/15107303/openapi.do 활용신청
- 키를 `.env` 의 `MOIS_OPENAPI_KEY=` 에 입력
- `python scripts/load_population_kosis.py` 실행

---

## 다운로드 후 할 일

```powershell
python scripts/compare_population_sources.py
```

산출물:
- `data/raw/population/comparison_report.md` — 매칭률, 컬럼 풍부도, 인구 수치 차이 비교
- 콘솔에 어느 소스가 가장 적합한지 추천

이 보고서 보고 어느 소스 채택할지 결정 → 단계 3 (DB 통합) 진행.

---

## 데이터 매칭 키

우리 `districts` 테이블 (창원시 56개 행정동) 과 매칭:
- 정규화: 공백 제거 후 (시군구, 행정동) 일치
- 예) `"창원시 마산합포구 가포동"` ↔ `"창원시마산합포구"` + `"가포동"`

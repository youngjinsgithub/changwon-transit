"""창원 행정동 인구 데이터 소스 비교.

data/raw/population/ 에 있는 CSV 들을 자동 감지해서 비교:
  - mois_*.csv     : 행정안전부 (수동 다운로드)
  - sgis_*.csv     : 통계청 SGIS API
  - kosis_*.csv, bigdata_*.csv : KOSIS / 창원 빅데이터 등

비교 항목:
  1. 행정동 매칭률 (우리 districts 테이블과 join)
  2. 컬럼 풍부도 (총인구 / 성별 / 연령대)
  3. 같은 동 인구 수치 소스 간 차이
  4. 최신성 (파일 기준일)

출력: data/raw/population/comparison_report.md
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402

POP_DIR = PROJECT_ROOT / "data" / "raw" / "population"
REPORT = POP_DIR / "comparison_report.md"


SOURCE_LABEL = {
    "mois":    "행정안전부 (jumin.mois.go.kr)",
    "sgis":    "통계청 SGIS API",
    "kosis":   "KOSIS / 공공데이터 OpenAPI",
    "bigdata": "창원시 빅데이터 포털",
}


def normalize(s: str) -> str:
    """공백 제거 정규화 (시군구·동명 매칭용)."""
    return "".join(str(s or "").split())


def detect_columns(df: pd.DataFrame) -> dict:
    """소스마다 다른 컬럼명을 정규화된 키로 매핑.

    행안부(MOIS) 영문 컬럼 (sggNm, dongNm, totNmprCnt 등) 과
    한글 컬럼 (행정구역, 총인구수, 남자, 여자, 0-9세 등) 둘 다 인식.

    Returns:
        {
          'admin_col':   행정구역 단일 컬럼 (예: '행정구역') — 또는 None
          'sigungu_col': 시군구 별도 컬럼 (예: 'sggNm') — 있으면 admin_col 대신 사용
          'dong_col':    동명 별도 컬럼 (예: 'dongNm')
          'total_col':   총 인구 컬럼
          'male_col':    남자 인구
          'female_col':  여자 인구
          'age_cols':    연령대 컬럼 리스트
        }
    """
    cols = list(df.columns)
    out = {
        "admin_col": None, "sigungu_col": None, "dong_col": None,
        "total_col": None, "male_col": None, "female_col": None, "age_cols": [],
    }

    for c in cols:
        s = str(c).strip()

        # 영문 컬럼 (MOIS 행안부 표준)
        if s == "sggNm":
            out["sigungu_col"] = c
            continue
        if s == "dongNm":
            out["dong_col"] = c
            continue
        if s == "totNmprCnt":
            out["total_col"] = c
            continue
        if s == "maleNmprCnt":
            out["male_col"] = c
            continue
        if s == "femlNmprCnt":
            out["female_col"] = c
            continue
        if re.match(r"^(male|feml)\d+AgeNmprCnt$", s):
            out["age_cols"].append(c)
            continue

        # 한글 컬럼 (다른 소스 / 사이트 직접 다운로드)
        if out["admin_col"] is None and any(k in s for k in ["행정구역", "지역", "동", "읍면동", "adm"]):
            out["admin_col"] = c
        if out["total_col"] is None and any(k in s for k in ["총인구", "인구수", "인구계", "계"]) and "남" not in s and "여" not in s:
            out["total_col"] = c
        if out["male_col"] is None and ("남" in s and "남여" not in s):
            out["male_col"] = c
        if out["female_col"] is None and "여" in s and "남여" not in s and "여자" in s.replace(" ", "") + "여자":
            out["female_col"] = c
        if re.search(r"\d+\s*[~-]\s*\d+\s*세|\d+\s*세\s*이상|^\d+세$", s):
            out["age_cols"].append(c)

    return out


def load_source(path: Path) -> dict:
    """CSV 로드 + 정규화. 다양한 인코딩 시도."""
    raw = None
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            raw = pd.read_csv(path, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        return {"path": path, "error": "인코딩 실패", "df": None}

    spec = detect_columns(raw)

    # 케이스 1: 시군구·동이 별도 컬럼 (MOIS 영문 — sggNm, dongNm)
    if spec["sigungu_col"] and spec["dong_col"]:
        raw["__sigungu"] = raw[spec["sigungu_col"]].astype(str).str.strip()
        raw["__dong"] = raw[spec["dong_col"]].astype(str).str.strip()

        # 통/반 단위 분리되어 있으면 행정동 단위로 합산 (수치 컬럼만 SUM)
        numeric_cols = [c for c in [spec["total_col"], spec["male_col"],
                                      spec["female_col"]] + spec["age_cols"] if c]
        if numeric_cols:
            agg_dict = {c: "sum" for c in numeric_cols}
            # 다른 컬럼(예: hhCnt 세대수) 도 SUM 하면 좋겠지만 일단 보유 컬럼만
            for extra in ["hhCnt", "hhNmpr"]:
                if extra in raw.columns:
                    agg_dict[extra] = "sum"
            grouped = (
                raw.groupby(["__sigungu", "__dong"], as_index=False)
                .agg(agg_dict)
            )
            # __ 이름 보존 + 정규화 컬럼 추가
            grouped["__sigungu_norm"] = grouped["__sigungu"].apply(normalize)
            grouped["__dong_norm"] = grouped["__dong"].apply(normalize)
            print(f"    통/반 단위 → 행정동 단위 합산: {len(raw)} → {len(grouped)}")
            return {"path": path, "df": grouped, "spec": spec, "error": None}

        # 합산 대상 컬럼 없으면 그냥 dedup
        raw["__sigungu_norm"] = raw["__sigungu"].apply(normalize)
        raw["__dong_norm"] = raw["__dong"].apply(normalize)
        return {"path": path, "df": raw, "spec": spec, "error": None}

    # 케이스 2: 단일 행정구역 컬럼 (한글 — "경상남도 창원시 ..." 형식)
    if not spec["admin_col"]:
        return {"path": path, "error": "행정구역 컬럼 못찾음", "df": raw, "spec": spec}

    parts = raw[spec["admin_col"]].astype(str).str.split(r"\s+", expand=True)
    # 다양한 패턴 (예: "경상남도 창원시 마산합포구 가포동" 또는 "창원시마산합포구 가포동")
    # 일단 마지막 토큰을 동으로, 그 앞을 시군구로
    if parts.shape[1] >= 3:
        raw["__sigungu"] = parts.iloc[:, -2].fillna("") + " " + parts.iloc[:, -1].fillna("")
        raw["__dong"] = parts.iloc[:, -1]
        # 보다 정확하게: 창원시 + 구 / 또는 마지막 두 토큰 중 끝이 "동/읍/면" 이면 동, 그 앞이 구
        # 동/읍/면 인지 검사하여 보정
        last = parts.iloc[:, parts.shape[1] - 1].fillna("")
        is_dong = last.str.contains(r"동$|읍$|면$|리$", na=False)
        if is_dong.any():
            raw["__dong"] = last.where(is_dong, "")
            raw["__sigungu"] = parts.iloc[:, parts.shape[1] - 2].fillna("").where(
                is_dong, parts.iloc[:, -1].fillna("")
            )
            # 시군구 정확히 잡기 위해 "창원시" 토큰 검사
            for idx in raw.index:
                if not is_dong[idx]:
                    continue
                row_parts = [p for p in parts.loc[idx].dropna().tolist() if p]
                # 끝에서 두 번째가 구 (예: 마산합포구)
                if len(row_parts) >= 2:
                    sg = row_parts[-2]
                    # 그 앞에 창원시가 있는지
                    prefix = row_parts[-3] if len(row_parts) >= 3 else ""
                    if "창원" in prefix:
                        raw.at[idx, "__sigungu"] = f"창원시 {sg}"
                    elif "시" in sg or "구" in sg:
                        raw.at[idx, "__sigungu"] = sg
    elif parts.shape[1] == 2:
        raw["__sigungu"] = parts.iloc[:, 0]
        raw["__dong"] = parts.iloc[:, 1]
    else:
        raw["__sigungu"] = ""
        raw["__dong"] = parts.iloc[:, 0] if parts.shape[1] >= 1 else ""

    raw["__sigungu_norm"] = raw["__sigungu"].apply(normalize)
    raw["__dong_norm"] = raw["__dong"].apply(normalize)

    return {"path": path, "df": raw, "spec": spec, "error": None}


def load_districts(engine) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT district_id, sigungu, district_name FROM districts ORDER BY sigungu, district_name"),
            conn,
        )
    df["sg_norm"] = df["sigungu"].apply(normalize)
    df["dn_norm"] = df["district_name"].apply(normalize)
    return df


def match_to_districts(source_df: pd.DataFrame, districts: pd.DataFrame, total_col: str | None):
    """소스 데이터 ↔ districts 매칭, 매칭 행수·평균 인구 등 통계 반환."""
    # 창원 데이터만 필터: 시군구에 "창원" 포함
    cw = source_df[source_df["__sigungu_norm"].str.contains("창원", na=False)].copy()
    if cw.empty:
        return {"n_source_rows": 0, "n_changwon": 0, "n_matched": 0,
                "match_pct": 0.0, "unmatched_dong": [], "matched": None}

    # join on (sigungu_norm, dong_norm)
    merged = cw.merge(
        districts[["sg_norm", "dn_norm", "district_name", "sigungu"]],
        left_on=["__sigungu_norm", "__dong_norm"],
        right_on=["sg_norm", "dn_norm"],
        how="left",
    )
    matched = merged["sg_norm"].notna().sum()

    unmatched = merged[merged["sg_norm"].isna()][["__sigungu", "__dong"]].drop_duplicates()
    unmatched_dong = unmatched.head(10).apply(
        lambda r: f"{r['__sigungu']}·{r['__dong']}", axis=1
    ).tolist()

    return {
        "n_source_rows": len(source_df),
        "n_changwon": len(cw),
        "n_matched": int(matched),
        "match_pct": (matched / len(cw) * 100) if len(cw) else 0.0,
        "unmatched_dong": unmatched_dong,
        "matched": merged[merged["sg_norm"].notna()],
        "total_col": total_col,
    }


def main() -> int:
    print(f"[1] 소스 CSV 자동 감지: {POP_DIR}")
    csv_files = sorted(POP_DIR.glob("*.csv"))
    if not csv_files:
        print("    [ERR] CSV 파일 없음. data/raw/population/ 에 다운로드 후 다시 실행.")
        print(f"    가이드: {POP_DIR}/README.md")
        return 1

    sources: dict[str, dict] = {}
    for f in csv_files:
        # prefix 로 소스 식별
        prefix = f.stem.split("_")[0].lower()
        if prefix not in SOURCE_LABEL:
            print(f"    스킵 (알 수 없는 prefix): {f.name}")
            continue
        print(f"    감지: {f.name}  → 소스 [{prefix}] {SOURCE_LABEL[prefix]}")
        sources[prefix] = load_source(f)
        sources[prefix]["prefix"] = prefix

    if not sources:
        print("    [ERR] 인식 가능한 소스 없음 (파일명이 mois_/sgis_/kosis_/bigdata_ 로 시작해야 함)")
        return 1

    print(f"\n[2] districts 테이블 로드")
    engine = get_engine()
    districts = load_districts(engine)
    print(f"    창원 행정동 {len(districts)}개")

    print(f"\n[3] 소스별 매칭·풍부도 계산")
    results = {}
    for prefix, s in sources.items():
        if s.get("error"):
            print(f"    [{prefix}] 에러: {s['error']}")
            results[prefix] = {**s, "match_stats": None}
            continue
        stats = match_to_districts(s["df"], districts, s["spec"]["total_col"])
        results[prefix] = {**s, "match_stats": stats}
        print(f"    [{prefix}] 매칭 {stats['n_matched']}/{stats['n_changwon']} ({stats['match_pct']:.1f}%)")

    print(f"\n[4] 리포트 작성")
    lines = []
    lines.append("# 창원 행정동 인구 데이터 소스 비교 리포트")
    lines.append("")
    lines.append(f"비교 일자: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"창원 행정동 (districts 테이블 기준): **{len(districts)}개**")
    lines.append("")

    # 매칭률·풍부도 테이블
    lines.append("## 1. 소스별 요약")
    lines.append("")
    lines.append("| 소스 | 파일 | 창원 행 | 매칭 | 매칭률 | 총인구컬럼 | 성별구분 | 연령컬럼수 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for prefix in sorted(results.keys()):
        r = results[prefix]
        label = SOURCE_LABEL[prefix]
        if r.get("error"):
            lines.append(f"| {label} | {r['path'].name} | ERR | ERR | - | - | - | - |")
            continue
        ms = r["match_stats"]
        spec = r["spec"]
        gender = "✓" if (spec["male_col"] and spec["female_col"]) else "—"
        lines.append(
            f"| {label} | `{r['path'].name}` | {ms['n_changwon']} | {ms['n_matched']} | "
            f"{ms['match_pct']:.1f}% | "
            f"{'✓ `'+spec['total_col']+'`' if spec['total_col'] else '✗'} | "
            f"{gender} | {len(spec['age_cols'])} |"
        )
    lines.append("")

    # 미매칭 동
    lines.append("## 2. 매칭 안 된 동 샘플 (각 소스 최대 10개)")
    lines.append("")
    for prefix in sorted(results.keys()):
        r = results[prefix]
        if r.get("error") or not r["match_stats"]:
            continue
        unmatched = r["match_stats"]["unmatched_dong"]
        lines.append(f"### {SOURCE_LABEL[prefix]}")
        if not unmatched:
            lines.append("- 미매칭 없음 ✓")
        else:
            for u in unmatched:
                lines.append(f"- {u}")
        lines.append("")

    # 인구 수치 비교 (같은 동)
    lines.append("## 3. 같은 동 인구 수치 소스 간 차이 (총인구 기준)")
    lines.append("")

    # 모든 소스에 총인구 컬럼이 있는 것만 비교
    valid = {p: r for p, r in results.items()
             if r.get("match_stats") and r["spec"]["total_col"] and not r.get("error")}
    if len(valid) < 2:
        lines.append("_비교 가능한 소스가 2개 미만입니다._")
    else:
        # 동별 인구 dict
        per_source = {}
        for prefix, r in valid.items():
            ms = r["match_stats"]
            matched = ms["matched"]
            tcol = r["spec"]["total_col"]
            # 숫자 변환 (콤마 제거)
            matched["__pop"] = (
                matched[tcol].astype(str).str.replace(",", "").str.strip()
                .pipe(pd.to_numeric, errors="coerce")
            )
            d = (
                matched.groupby(["sigungu", "district_name"])["__pop"]
                .sum().reset_index()
            )
            d = d.rename(columns={"__pop": prefix})
            per_source[prefix] = d.set_index(["sigungu", "district_name"])[prefix]

        comp = pd.concat(per_source.values(), axis=1)
        comp.columns = list(per_source.keys())
        comp["range"] = comp.max(axis=1) - comp.min(axis=1)
        comp["range_pct"] = (comp["range"] / comp.mean(axis=1) * 100).round(1)
        comp = comp.sort_values("range_pct", ascending=False)

        lines.append("**가장 차이 큰 동 TOP 10** (range_pct = 최대값-최소값 / 평균값)")
        lines.append("")
        lines.append(comp.head(10).to_markdown())
        lines.append("")
        lines.append(f"**전체 평균 차이율**: {comp['range_pct'].mean():.2f}% (작을수록 소스 간 일치)")
        lines.append("")

    # 추천
    lines.append("## 4. 추천")
    lines.append("")
    best = None
    for prefix, r in results.items():
        if r.get("error") or not r["match_stats"]:
            continue
        score = (
            r["match_stats"]["match_pct"]
            + (10 if r["spec"]["total_col"] else -50)
            + (5 if r["spec"]["male_col"] else 0)
            + (len(r["spec"]["age_cols"]) * 0.5)
        )
        if best is None or score > best[1]:
            best = (prefix, score)

    if best:
        lines.append(f"**점수 1위**: `{best[0]}` = {SOURCE_LABEL[best[0]]}  (score {best[1]:.1f})")
        lines.append("- 점수 = 매칭률 + (총인구 컬럼 +10) + (성별 컬럼 +5) + (연령컬럼 ×0.5)")
    else:
        lines.append("_유효한 소스 없음._")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[5] 저장: {REPORT.relative_to(PROJECT_ROOT)}")
    print(f"\n--- 리포트 미리보기 ---")
    print("\n".join(lines[:40]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

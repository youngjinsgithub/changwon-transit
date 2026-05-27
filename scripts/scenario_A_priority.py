# =============================================================================
# 창원시 버스 노선 개편 우선순위 산정 알고리즘
# 시나리오 A : 효율성 중심 모델
#   목적 : 자가용 이용자를 대중교통으로 유인하여
#          유류비 절감 및 탄소 감축 극대화
#
# 제출처 : 2026 경상남도 빅데이터 공모전
# 외부 라이브러리 : pandas, numpy 만 사용
# =============================================================================

import numpy as np
import pandas as pd

# -----------------------------------------------------------------------------
# ▶ 더미 데이터 (Toy Dataset) — 실제 분석 시 DB 쿼리 결과 DataFrame 으로 교체
#   각 행은 창원시의 버스 노선 1개를 나타낸다.
# -----------------------------------------------------------------------------
data = {
    "route_id": ["105", "102", "BRT5000", "710", "113"],

    # 노선이 통과하는 주요 도로의 자가용 통행량 합계
    # → 높을수록 자가용 수요가 크므로 개편 우선순위 ↑
    "traffic_volume": [12500.0, 9800.0, 18000.0, 4200.0, 7600.0],

    # 노선 정류장들의 총 승차 수요량 (STCIS 집계)
    # → 높을수록 버스 수요가 크므로 개편 우선순위 ↑
    "passenger_count": [158270.0, 142935.0, 95000.0, 119839.0, 126191.0],

    # 현재 배차간격 중앙값 (분)
    # → 간격이 길수록 서비스 불편 → 개편 우선순위 ↑
    "headway_minutes": [18.0, 22.0, 8.0, 35.0, 14.0],

    # 외곽·교통약자 소외도 지수 (0~1)
    # → 높을수록 취약 지역 → 개편 우선순위 ↑
    "vulnerability_index": [0.32, 0.41, 0.15, 0.78, 0.55],

    # 노선 경유지 거주 인구수
    # → 역산(Negative Scaling) 적용:
    #   인구가 너무 많은 도심 핵심지는 이미 기본 서비스가 양호하므로
    #   인구 대비 공급이 엇박자 나는 지역을 잡기 위해 역방향 스케일링
    "population": [57580.0, 50872.0, 52348.0, 48870.0, 48870.0],

    # 노선 경유지 세대수 (다중공선성 제거 대상)
    "household_count": [24000.0, 21000.0, 22500.0, 20000.0, 20500.0],
}

df_bus = pd.DataFrame(data)

print("=" * 60)
print("  창원시 버스 노선 개편 우선순위 산정 — 시나리오 A")
print("=" * 60)
print("\n[원본 데이터]")
print(df_bus.to_string(index=False))


# =============================================================================
# [단계 1] 전처리 : 다중공선성(Multicollinearity) 방지
# =============================================================================
# household_count(세대수)는 population(인구수)과 높은 양의 선형 관계를 가져
# 두 변수를 동시에 모델에 포함하면 가중치가 중복 반영되어 분석이 왜곡된다.
# (VIF 검증 시 household_count 의 VIF 값이 10 이상으로 나타나는 경우 해당)
# → household_count 열을 완전히 제거하여 다중공선성 문제를 선제적으로 방지한다.

df = df_bus.drop(columns=["household_count"])

print("\n[단계 1] household_count 드롭 완료 → 남은 열:", list(df.columns))


# =============================================================================
# [단계 2] Min-Max 정규화 (sklearn 미사용, Pandas 연산으로 직접 구현)
# =============================================================================
# 변수마다 단위·크기가 달라 직접 가중합산하면 큰 값의 변수가 결과를 지배한다.
# Min-Max Scaler 로 모든 변수를 [0, 1] 구간으로 통일한다.

def minmax_pos(series: pd.Series) -> pd.Series:
    """
    Positive(정방향) 스케일링
    지표가 클수록 개편 시급 → 값이 클수록 1에 가깝게 변환
    수식 : X_scaled = (X - X_min) / (X_max - X_min)
    """
    x_min, x_max = series.min(), series.max()
    if x_max == x_min:          # 모든 값이 동일할 때 0으로 처리(분모 0 방지)
        return pd.Series(0.0, index=series.index)
    return (series - x_min) / (x_max - x_min)


def minmax_neg(series: pd.Series) -> pd.Series:
    """
    Negative(역방향) 스케일링
    지표가 클수록 이미 수요 기반 편익이 제공된 것 → 값이 클수록 0에 가깝게 변환
    수식 : X_scaled = (X_max - X) / (X_max - X_min)
    적용 대상 : population
      (도심 고인구 지역은 기본 서비스가 이미 충분하므로,
       인구 대비 공급이 부족한 지역이 상위권에 오도록 역산)
    """
    x_min, x_max = series.min(), series.max()
    if x_max == x_min:
        return pd.Series(0.0, index=series.index)
    return (x_max - series) / (x_max - x_min)


# --- Positive 스케일링 적용 변수 ---
df["traffic_volume_scaled"]    = minmax_pos(df["traffic_volume"])
df["passenger_count_scaled"]   = minmax_pos(df["passenger_count"])
df["headway_minutes_scaled"]   = minmax_pos(df["headway_minutes"])
df["vulnerability_index_scaled"] = minmax_pos(df["vulnerability_index"])

# --- Negative 스케일링 적용 변수 ---
df["population_scaled"]        = minmax_neg(df["population"])

print("\n[단계 2] 정규화 결과 (소수점 4자리)")
scaled_cols = [
    "route_id",
    "traffic_volume_scaled",
    "passenger_count_scaled",
    "headway_minutes_scaled",
    "vulnerability_index_scaled",
    "population_scaled",
]
print(df[scaled_cols].round(4).to_string(index=False))


# =============================================================================
# [단계 3] 시나리오 A 가중치 결합 → priority_score 산출
# =============================================================================
# 각 정규화 변수에 시나리오 A 가중치를 곱해 최종 우선순위 점수를 계산한다.
# 가중치 합계 = 0.30 + 0.30 + 0.20 + 0.10 + 0.10 = 1.00 (검증 포함)

W_TRAFFIC      = 0.30   # 교통량    : 자가용 전환 잠재력이 가장 큰 요인
W_PASSENGER    = 0.30   # 버스 수요 : 실제 이용 수요가 높은 노선 우선
W_HEADWAY      = 0.20   # 배차간격  : 불편도가 클수록 개선 효과 큼
W_VULNERABILITY = 0.10  # 소외도    : 교통약자·외곽 배려
W_POPULATION   = 0.10   # 인구 역산 : 공급 대비 과소 수혜 지역 보정

# 가중치 합계 검증 (부동소수점 오차를 허용하여 확인)
weight_sum = W_TRAFFIC + W_PASSENGER + W_HEADWAY + W_VULNERABILITY + W_POPULATION
assert abs(weight_sum - 1.0) < 1e-9, f"가중치 합계 오류: {weight_sum}"

df["priority_score"] = (
    df["traffic_volume_scaled"]      * W_TRAFFIC      +
    df["passenger_count_scaled"]     * W_PASSENGER    +
    df["headway_minutes_scaled"]     * W_HEADWAY      +
    df["vulnerability_index_scaled"] * W_VULNERABILITY +
    df["population_scaled"]          * W_POPULATION
)

print("\n[단계 3] 가중치 적용 완료")
print(f"  가중치 구성: 교통량 {W_TRAFFIC*100:.0f}% | 승차수요 {W_PASSENGER*100:.0f}%"
      f" | 배차간격 {W_HEADWAY*100:.0f}% | 소외도 {W_VULNERABILITY*100:.0f}%"
      f" | 인구역산 {W_POPULATION*100:.0f}%")


# =============================================================================
# [단계 4] 최종 정렬 및 출력
# =============================================================================

# priority_score 기준 내림차순 정렬 (점수 높을수록 개편 우선순위 높음)
df_result = (
    df.sort_values("priority_score", ascending=False)
      .reset_index(drop=True)
)
df_result.index = df_result.index + 1          # 순위를 1부터 시작
df_result.index.name = "rank"

# 출력할 열 선택 (가독성을 위해 스케일링 중간 열 포함)
output_cols = [
    "route_id",
    "traffic_volume", "passenger_count", "headway_minutes",
    "vulnerability_index", "population",
    "traffic_volume_scaled", "passenger_count_scaled",
    "headway_minutes_scaled", "vulnerability_index_scaled", "population_scaled",
    "priority_score",
]

print("\n[단계 4] 우선순위 TOP 10")
print("-" * 60)
top10 = df_result[output_cols].head(10)
# 소수점 표시 통일
fmt = {c: "{:.4f}" for c in output_cols if c.endswith("_scaled") or c == "priority_score"}
print(top10.to_string(float_format=lambda x: f"{x:.4f}"))

# CSV 저장 (전체 결과)
csv_path = "changwon_bus_priority_scenario_A.csv"
df_result[output_cols].to_csv(csv_path, encoding="utf-8-sig")
print(f"\n전체 결과 저장 완료 → {csv_path}")
print("=" * 60)

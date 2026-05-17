-- ============================================================================
-- 창원 대중교통 데이터 분석 - 스키마 정의
-- 전부 멱등(IF NOT EXISTS) 으로 작성되어 재실행 안전
-- 좌표계: WGS84 (EPSG:4326)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 확장
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------
-- routes : 노선 마스터
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS routes (
    route_id    SERIAL       PRIMARY KEY,
    route_no    VARCHAR(20)  NOT NULL,
    route_name  VARCHAR(200),
    route_type  VARCHAR(20),
    color       VARCHAR(7),
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_routes_no ON routes(route_no);

COMMENT ON TABLE  routes              IS '버스 노선 마스터';
COMMENT ON COLUMN routes.route_id     IS '내부 노선 ID (서로게이트 키)';
COMMENT ON COLUMN routes.route_no     IS '노선 번호 (예: 100, 3000, 좌석5006)';
COMMENT ON COLUMN routes.route_name   IS '노선명 (기점 ↔ 종점)';
COMMENT ON COLUMN routes.route_type   IS '노선 등급: 급행좌석/간선/지선/읍면/마을';
COMMENT ON COLUMN routes.color        IS '노선 색상 #RRGGBB (시각화용)';
COMMENT ON COLUMN routes.created_at   IS '레코드 생성 시각';

-- ---------------------------------------------------------------------------
-- stops : 정류장 마스터 (좌표·식별자 통합)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stops (
    stop_id     VARCHAR(20)  PRIMARY KEY,
    stop_name   VARCHAR(200) NOT NULL,
    bis_number  VARCHAR(20),
    tago_id     VARCHAR(50),
    stcis_id    VARCHAR(50),
    latitude    NUMERIC(10, 7),
    longitude   NUMERIC(10, 7),
    location    GEOMETRY(POINT, 4326),
    district    VARCHAR(50),
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stops_geom     ON stops USING GIST(location);
CREATE INDEX IF NOT EXISTS idx_stops_bis      ON stops(bis_number);
CREATE INDEX IF NOT EXISTS idx_stops_district ON stops(district);

COMMENT ON TABLE  stops             IS '정류장 마스터 (소스별 ID 통합)';
COMMENT ON COLUMN stops.stop_id     IS '프로젝트 통합 정류장 ID (자체 부여)';
COMMENT ON COLUMN stops.stop_name   IS '정류장 명칭';
COMMENT ON COLUMN stops.bis_number  IS '창원 BIS 정류장 번호';
COMMENT ON COLUMN stops.tago_id     IS '국토부 TAGO 정류장 ID';
COMMENT ON COLUMN stops.stcis_id    IS 'STCIS(승하차 통계) 정류장 ID';
COMMENT ON COLUMN stops.latitude    IS '위도 (WGS84)';
COMMENT ON COLUMN stops.longitude   IS '경도 (WGS84)';
COMMENT ON COLUMN stops.location    IS 'PostGIS POINT 지오메트리 (SRID 4326)';
COMMENT ON COLUMN stops.district    IS '행정구/동 (의창/성산/마산합포/마산회원/진해)';
COMMENT ON COLUMN stops.created_at  IS '레코드 생성 시각';

-- ---------------------------------------------------------------------------
-- route_stops : 노선-정류장 매핑 (경유 순서·방향)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS route_stops (
    route_id   INTEGER     REFERENCES routes(route_id),
    stop_id    VARCHAR(20) REFERENCES stops(stop_id),
    sequence   INTEGER     NOT NULL,
    direction  VARCHAR(10),
    -- 회차 노선에서 같은 정류소를 같은 방향으로 두 번 지날 수 있어
    -- sequence 도 PK 에 포함 (관측: TAGO 창원 노선 데이터에서 중복 다수 확인 2026-05-13)
    PRIMARY KEY (route_id, stop_id, direction, sequence)
);
CREATE INDEX IF NOT EXISTS idx_route_stops_route ON route_stops(route_id);
CREATE INDEX IF NOT EXISTS idx_route_stops_stop  ON route_stops(stop_id);

COMMENT ON TABLE  route_stops            IS '노선의 경유 정류장 (순서·방향 포함)';
COMMENT ON COLUMN route_stops.route_id   IS '노선 ID (routes.route_id 참조)';
COMMENT ON COLUMN route_stops.stop_id    IS '정류장 ID (stops.stop_id 참조)';
COMMENT ON COLUMN route_stops.sequence   IS '노선 내 순서 (1부터 시작)';
COMMENT ON COLUMN route_stops.direction  IS '진행 방향: 상행/하행';

-- ---------------------------------------------------------------------------
-- stop_distances : 정류장 간 거리 (Haversine 또는 노선상 거리)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stop_distances (
    from_stop_id   VARCHAR(20)   REFERENCES stops(stop_id),
    to_stop_id     VARCHAR(20)   REFERENCES stops(stop_id),
    distance_m     NUMERIC(10, 2),
    distance_type  VARCHAR(20),
    PRIMARY KEY (from_stop_id, to_stop_id, distance_type)
);
CREATE INDEX IF NOT EXISTS idx_distances_from ON stop_distances(from_stop_id);
CREATE INDEX IF NOT EXISTS idx_distances_dist ON stop_distances(distance_m);

COMMENT ON TABLE  stop_distances                IS '정류장 쌍 거리 메타데이터 (직선/노선상)';
COMMENT ON COLUMN stop_distances.from_stop_id   IS '출발 정류장 ID';
COMMENT ON COLUMN stop_distances.to_stop_id     IS '도착 정류장 ID';
COMMENT ON COLUMN stop_distances.distance_m     IS '거리 (미터)';
COMMENT ON COLUMN stop_distances.distance_type  IS '거리 종류: haversine / route';

-- ---------------------------------------------------------------------------
-- boarding_data : 정류장별 시간대별 승하차 (STCIS 롱 포맷)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS boarding_data (
    stop_id    VARCHAR(20)  REFERENCES stops(stop_id),
    date       DATE         NOT NULL,
    hour       SMALLINT     NOT NULL CHECK (hour BETWEEN 0 AND 23),
    boarding   INTEGER      DEFAULT 0,
    alighting  INTEGER      DEFAULT 0,
    PRIMARY KEY (stop_id, date, hour)
);
CREATE INDEX IF NOT EXISTS idx_boarding_date      ON boarding_data(date);
CREATE INDEX IF NOT EXISTS idx_boarding_date_hour ON boarding_data(date, hour);
CREATE INDEX IF NOT EXISTS idx_boarding_stop_date ON boarding_data(stop_id, date);

COMMENT ON TABLE  boarding_data            IS '정류장별 일자·시간대별 승하차 인원 (STCIS 기반)';
COMMENT ON COLUMN boarding_data.stop_id    IS '정류장 ID';
COMMENT ON COLUMN boarding_data.date       IS '집계 일자';
COMMENT ON COLUMN boarding_data.hour       IS '시간대 (0~23)';
COMMENT ON COLUMN boarding_data.boarding   IS '승차 인원';
COMMENT ON COLUMN boarding_data.alighting  IS '하차 인원';

-- ---------------------------------------------------------------------------
-- boarding_data 확장 (STCIS 매핑 통합 시점: 2026-05-14)
--   - boarding/alighting INT → NUMERIC: 평균값 사용으로 소수 발생 가능
--   - n_sttn_ids / n_tago_in_grp: 매핑 메타 (분석 시 신뢰도 지표)
-- 단일 boarding 값 = SUM(매칭 STCIS sttn_ids) ÷ n_tago_in_grp
--   → 단일 정류장: 그 값 그대로
--   → 상하행 페어/N개 그룹: 그룹 크기로 나눠 공유 (더블카운팅 자동 방지)
-- ---------------------------------------------------------------------------
ALTER TABLE boarding_data
    ALTER COLUMN boarding  TYPE NUMERIC(10, 2) USING boarding::numeric,
    ALTER COLUMN alighting TYPE NUMERIC(10, 2) USING alighting::numeric;

ALTER TABLE boarding_data
    ADD COLUMN IF NOT EXISTS n_sttn_ids    SMALLINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS n_tago_in_grp SMALLINT DEFAULT 0;

COMMENT ON COLUMN boarding_data.boarding       IS '승차 인원 (그룹 크기로 보정, 합산 시 STCIS 실제 총량과 일치)';
COMMENT ON COLUMN boarding_data.alighting      IS '하차 인원 (그룹 크기로 보정)';
COMMENT ON COLUMN boarding_data.n_sttn_ids     IS '이 TAGO stop 에 매핑된 STCIS sttn_id 개수';
COMMENT ON COLUMN boarding_data.n_tago_in_grp  IS '같은 STCIS sttn_id 집합을 공유하는 TAGO stop 수 (상하행 페어=2)';

-- ---------------------------------------------------------------------------
-- stcis_boarding_raw : STCIS sttn_id 단위 원본 (source of truth)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stcis_boarding_raw (
    stcis_sttn_id   BIGINT       NOT NULL,
    date            DATE         NOT NULL,
    hour            SMALLINT     NOT NULL CHECK (hour BETWEEN 0 AND 23),
    boarding        INTEGER      NOT NULL DEFAULT 0,
    alighting       INTEGER      NOT NULL DEFAULT 0,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (stcis_sttn_id, date, hour)
);
CREATE INDEX IF NOT EXISTS idx_stcis_raw_date ON stcis_boarding_raw(date);
CREATE INDEX IF NOT EXISTS idx_stcis_raw_sttn ON stcis_boarding_raw(stcis_sttn_id);

COMMENT ON TABLE  stcis_boarding_raw                IS 'STCIS 정류장 이용량 원본 (sttn_id 키, 가공 전 source of truth)';
COMMENT ON COLUMN stcis_boarding_raw.stcis_sttn_id  IS 'STCIS 내부 정류장 ID';
COMMENT ON COLUMN stcis_boarding_raw.date           IS '집계 일자';
COMMENT ON COLUMN stcis_boarding_raw.hour           IS '시간대 0~23';
COMMENT ON COLUMN stcis_boarding_raw.boarding       IS '승차 인원';
COMMENT ON COLUMN stcis_boarding_raw.alighting      IS '하차 인원';
COMMENT ON COLUMN stcis_boarding_raw.fetched_at     IS '수집 시각';

-- ---------------------------------------------------------------------------
-- stcis_stop_mapping : TAGO ↔ STCIS 매핑
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stcis_stop_mapping (
    tago_stop_id    VARCHAR(20) NOT NULL REFERENCES stops(stop_id),
    stcis_sttn_id   BIGINT      NOT NULL,
    match_tier      VARCHAR(20) NOT NULL,  -- 동일치 / 시군구만일치
    PRIMARY KEY (tago_stop_id, stcis_sttn_id)
);
CREATE INDEX IF NOT EXISTS idx_stcis_map_sttn ON stcis_stop_mapping(stcis_sttn_id);
CREATE INDEX IF NOT EXISTS idx_stcis_map_tier ON stcis_stop_mapping(match_tier);

COMMENT ON TABLE  stcis_stop_mapping               IS 'TAGO stop_id ↔ STCIS sttn_id 매핑 (data/raw/stcis/stop_mapping.csv 적재)';
COMMENT ON COLUMN stcis_stop_mapping.tago_stop_id  IS 'TAGO 정류장 ID (stops.stop_id 참조)';
COMMENT ON COLUMN stcis_stop_mapping.stcis_sttn_id IS 'STCIS 정류장 ID';
COMMENT ON COLUMN stcis_stop_mapping.match_tier    IS '매칭 신뢰도 등급';

-- ---------------------------------------------------------------------------
-- districts : 행정동 폴리곤 (공간 조인용, 선택)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS districts (
    district_id    SERIAL       PRIMARY KEY,
    district_name  VARCHAR(50),
    sigungu        VARCHAR(50),
    -- 행정동은 다도해·하천 등으로 MultiPolygon 인 경우 다수
    geometry       GEOMETRY(MULTIPOLYGON, 4326)
);
CREATE INDEX IF NOT EXISTS idx_districts_geom ON districts USING GIST(geometry);

COMMENT ON TABLE  districts                IS '행정동 경계 폴리곤 (공간 조인용)';
COMMENT ON COLUMN districts.district_id    IS '행정동 내부 ID';
COMMENT ON COLUMN districts.district_name  IS '행정동명 (예: 의창동)';
COMMENT ON COLUMN districts.sigungu        IS '시·군·구 명';
COMMENT ON COLUMN districts.geometry       IS '폴리곤 지오메트리 (SRID 4326)';

-- districts 인구 확장 (행안부 mois CSV 적재, 2026-05-17)
ALTER TABLE districts
    ADD COLUMN IF NOT EXISTS population       INT,
    ADD COLUMN IF NOT EXISTS households       INT,
    ADD COLUMN IF NOT EXISTS pop_male         INT,
    ADD COLUMN IF NOT EXISTS pop_female       INT,
    ADD COLUMN IF NOT EXISTS pop_age_0_19     INT,
    ADD COLUMN IF NOT EXISTS pop_age_20_64    INT,
    ADD COLUMN IF NOT EXISTS pop_age_65_plus  INT,
    ADD COLUMN IF NOT EXISTS pop_date         DATE;

COMMENT ON COLUMN districts.population       IS '총 인구수 (주민등록)';
COMMENT ON COLUMN districts.households       IS '세대수';
COMMENT ON COLUMN districts.pop_male         IS '남자 인구';
COMMENT ON COLUMN districts.pop_female       IS '여자 인구';
COMMENT ON COLUMN districts.pop_age_0_19     IS '0~19세 (학령·청소년기)';
COMMENT ON COLUMN districts.pop_age_20_64    IS '20~64세 (생산가능)';
COMMENT ON COLUMN districts.pop_age_65_plus  IS '65세 이상 (고령)';
COMMENT ON COLUMN districts.pop_date         IS '인구 통계 기준일';

"""
AI Productivity Investment Dashboard — 데이터 수집 스크립트
=============================================================
실행: python collect.py            → data.json 생성/갱신
GitHub Actions(.github/workflows/update-data.yml)에서 매일 자동 실행됩니다.

수동 입력 데이터가 전혀 없습니다. 모든 지표를 아래 공개 소스에서 직접 수집합니다.

  FRED (미국 금리·물가·침체확률·VIX·생산성·부채)   - 키 불필요(CSV) / 선택(JSON API)
  ECOS 한국은행 (기준금리·국고채 10년·CPI)          - 인증키 필요
  SEC EDGAR (Hyperscaler CAPEX, Micron 매출)        - 키 불필요, User-Agent 필수
  pykrx (KOSPI EPS·배당수익률·기술적 추세, RIM 입력) - 키 불필요
  yfinance (QQQ/XLK/SOXX/S&P500 가격, PER)          - 키 불필요

환경변수:
  FRED_API_KEY  선택. 없으면 fredgraph.csv로 폴백.
  ECOS_API_KEY  필수(한국 지표 자동 수집). 없으면 해당 3개 지표만 결측 처리.

실패한 지표는 null로 남기고 errors 리스트에 사유를 기록합니다.
(수동 기본값으로 대체하지 않음 — 프론트엔드가 결측 지표를 자동으로 제외하고 재계산합니다.)
"""
from __future__ import annotations
import os
import sys
import json
import time
import datetime as dt
import urllib.request
import urllib.parse

import requests

# ──────────────────────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────────────────────
SEC_HEADERS = {"User-Agent": "AI-Productivity-Dashboard research-contact@example.com"}
HTTP_TIMEOUT = 25

ERRORS: list[str] = []


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def safe(name: str, fn, *args, **kwargs):
    """예외를 삼키고 실패 사유를 ERRORS에 기록. 실패 시 None 반환."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        msg = f"{name}: {e}"
        ERRORS.append(msg)
        log(f"[FAIL] {msg}")
        return None


def round2(v: float) -> float:
    return round(float(v), 2)


# ──────────────────────────────────────────────────────────────
# 1. FRED (미국 매크로)
#    - 순수 함수(parse_fred_csv / calc_fred_value)로 분리해 단위 테스트 용이하게 구성
# ──────────────────────────────────────────────────────────────
FRED_SERIES = {
    "us10y":           {"id": "DGS10",          "calc": "level", "months": 13, "history": True,  "label": "미국 10년물 국채금리 (%)"},
    "vix":             {"id": "VIXCLS",         "calc": "level", "months": 13, "history": True,  "label": "VIX"},
    "corePce":         {"id": "PCEPILFE",       "calc": "yoy",   "months": 30, "history": False, "label": "Core PCE YoY (%)"},
    "recessionProb":   {"id": "RECPROUSM156N",  "calc": "level", "months": 18, "history": False, "label": "미국 경기침체 확률 (%)"},
    "productivity":    {"id": "OPHNFB",         "calc": "yoy",   "months": 40, "history": False, "label": "미국 노동생산성 YoY (%)"},
    "usHouseholdDebt": {"id": "HDTGPDUSQ163N",  "calc": "level", "months": 30, "history": False, "label": "미국 가계부채/GDP (%)"},
    "krHouseholdDebt": {"id": "HDTGPDKRQ163N",  "calc": "level", "months": 30, "history": False, "label": "한국 가계부채/GDP (%)"},
    "corpDebtUS":      {"id": "QUSNAM770A",     "calc": "level", "months": 30, "history": False, "label": "미국 비금융기업 신용/GDP (%)"},
}


def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    obs = []
    for line in text.strip().splitlines()[1:]:
        d, _, v = line.partition(",")
        d = d.strip()
        try:
            val = float(v)
        except ValueError:
            continue
        if len(d) == 10 and d[4] == "-" and d[7] == "-":
            obs.append((d, val))
    return obs


def parse_fred_json(data: dict) -> list[tuple[str, float]]:
    if data.get("error_message"):
        raise RuntimeError(data["error_message"])
    obs = []
    for o in data.get("observations", []):
        try:
            obs.append((o["date"], float(o["value"])))
        except (ValueError, KeyError):
            continue
    return obs


def calc_series_value(obs: list[tuple[str, float]], calc: str) -> tuple[float, str]:
    """level: 최신값 그대로 / yoy: 최신값 대비 ~12개월 전 값 변화율(%)."""
    if not obs:
        raise RuntimeError("관측치 없음")
    obs = sorted(obs, key=lambda o: o[0])
    latest_d, latest_v = obs[-1]
    if calc == "level":
        return latest_v, latest_d
    target = dt.date.fromisoformat(latest_d).replace(year=dt.date.fromisoformat(latest_d).year - 1)
    base_d, base_v = min(obs, key=lambda o: abs((dt.date.fromisoformat(o[0]) - target).days))
    if base_v == 0:
        raise RuntimeError("YoY 기준값 0")
    return (latest_v / base_v - 1) * 100, latest_d


def fetch_fred_series(key: str, cfg: dict, api_key: str | None) -> dict:
    start = (dt.date.today() - dt.timedelta(days=cfg["months"] * 31)).isoformat()
    obs: list[tuple[str, float]] = []

    if api_key:
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": cfg["id"], "api_key": api_key, "file_type": "json", "observation_start": start},
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            obs = parse_fred_json(r.json())
        except Exception:
            obs = []  # 키 API 실패 시 CSV로 폴백

    if not obs:
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": cfg["id"], "cosd": start},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        obs = parse_fred_csv(r.text)

    value, as_of = calc_series_value(obs, cfg["calc"])
    out = {"label": cfg["label"], "seriesId": cfg["id"], "value": round2(value), "asOf": as_of}
    if cfg["history"]:
        obs_sorted = sorted(obs, key=lambda o: o[0])
        step = max(1, len(obs_sorted) // 130)
        hist = obs_sorted[::step]
        if hist[-1][0] != obs_sorted[-1][0]:
            hist.append(obs_sorted[-1])
        out["history"] = [{"d": d, "v": round2(v)} for d, v in hist]
    return out


# ──────────────────────────────────────────────────────────────
# 2. ECOS (한국은행) — 인증키 필요
# ──────────────────────────────────────────────────────────────
ECOS_SERIES = {
    "krBaseRate":    {"stat": "722Y001", "item": "0101000",    "period": "M", "calc": "level", "months": 6,  "label": "한국 기준금리 (%)"},
    "krTreasury10y": {"stat": "817Y002", "item": "010210000",  "period": "D", "calc": "level", "days": 20,   "label": "한국 국고채 10년 (%)"},
    "krCpi":         {"stat": "901Y009", "item": "0",          "period": "M", "calc": "yoy",   "months": 15, "label": "한국 CPI YoY (%)"},
}


def parse_ecos_json(data: dict) -> list[tuple[str, float]]:
    if "RESULT" in data:
        r = data["RESULT"]
        raise RuntimeError(r.get("MESSAGE") or r.get("CODE") or "ECOS 오류")
    rows = data.get("StatisticSearch", {}).get("row", [])
    obs = []
    for r in rows:
        try:
            obs.append((r["TIME"], float(r["DATA_VALUE"])))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(obs, key=lambda o: o[0])


def calc_ecos_value(obs: list[tuple[str, float]], calc: str) -> tuple[float, str]:
    if not obs:
        raise RuntimeError("관측치 없음")
    latest_t, latest_v = obs[-1]
    if calc == "level":
        return latest_v, latest_t
    # yoy: TIME이 YYYYMM(월간) 형식이라고 가정, 전년동월 검색
    ly = str(int(latest_t[:4]) - 1) + latest_t[4:]
    base = next((v for t, v in obs if t == ly), None)
    if base is None or base == 0:
        raise RuntimeError("YoY 기준월 없음")
    return (latest_v / base - 1) * 100, latest_t


def fetch_ecos_series(key: str, cfg: dict, ecos_key: str) -> dict:
    end = dt.date.today()
    if cfg["period"] == "D":
        start = end - dt.timedelta(days=cfg.get("days", 20))
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    else:
        start = end - dt.timedelta(days=cfg.get("months", 12) * 31)
        s, e = start.strftime("%Y%m"), end.strftime("%Y%m")

    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{urllib.parse.quote(ecos_key)}/json/kr/1/100/"
        f"{cfg['stat']}/{cfg['period']}/{s}/{e}/{cfg['item']}"
    )
    r = requests.get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    obs = parse_ecos_json(r.json())
    value, as_of_raw = calc_ecos_value(obs, cfg["calc"])
    as_of = f"{as_of_raw[:4]}-{as_of_raw[4:6]}" + (f"-{as_of_raw[6:8]}" if len(as_of_raw) == 8 else "")
    return {"label": cfg["label"], "stat": cfg["stat"], "value": round2(value), "asOf": as_of}


# ──────────────────────────────────────────────────────────────
# 3. SEC EDGAR — Hyperscaler CAPEX, Micron 매출 (HBM 수요 근사)
#    * 연간(10-K, FY) 데이터만 사용 — XBRL 분기 현금흐름 항목은 누적치라
#      분기 단위 차감 없이는 부정확하므로, 신뢰도 높은 연간 YoY로 계산
# ──────────────────────────────────────────────────────────────
HYPERSCALERS = {
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
}
MICRON_CIK = "0000723125"
CAPEX_TAG = "PaymentsToAcquirePropertyPlantAndEquipment"
REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]


def extract_annual_facts(concept_json: dict, unit: str = "USD") -> list[tuple[int, str, float]]:
    """10-K/FY 태그 중 회계기간이 350~380일(연간)인 사실만 추출, fy별 최신 값으로 중복 제거."""
    facts = concept_json.get("units", {}).get(unit, [])
    by_fy: dict[int, tuple[str, float]] = {}
    for f in facts:
        if f.get("form") != "10-K" or f.get("fp") != "FY":
            continue
        start, end, fy, val = f.get("start"), f.get("end"), f.get("fy"), f.get("val")
        if not (start and end and fy is not None and val is not None):
            continue
        try:
            days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        except ValueError:
            continue
        if 350 <= days <= 380:
            by_fy[fy] = (end, float(val))  # 같은 fy 재파일링 시 마지막 값으로 덮어씀
    return sorted((fy, end, val) for fy, (end, val) in by_fy.items())


def yoy_from_annual(rows: list[tuple[int, str, float]]) -> tuple[float, str]:
    if len(rows) < 2:
        raise RuntimeError("연간 데이터 2개년 미만")
    (_, _, v0), (_, end1, v1) = rows[-2], rows[-1]
    if v0 == 0:
        raise RuntimeError("전년도 값 0")
    return (v1 / v0 - 1) * 100, end1


def fetch_sec_concept(cik: str, tag: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
    r = requests.get(url, headers=SEC_HEADERS, timeout=HTTP_TIMEOUT)
    if r.status_code == 404:
        raise RuntimeError(f"{tag} 태그 없음")
    r.raise_for_status()
    return r.json()


def fetch_hyperscaler_capex() -> dict:
    prev_sum, latest_sum, latest_end = 0.0, 0.0, None
    used = []
    for name, cik in HYPERSCALERS.items():
        data = fetch_sec_concept(cik, CAPEX_TAG)
        time.sleep(0.15)  # SEC 권장: 초당 10회 이하
        rows = extract_annual_facts(data)
        if len(rows) < 2:
            raise RuntimeError(f"{name} 연간 데이터 부족")
        (_, _, v0), (_, end1, v1) = rows[-2], rows[-1]
        prev_sum += v0
        latest_sum += v1
        latest_end = end1 if latest_end is None or end1 > latest_end else latest_end
        used.append(name)
    if prev_sum == 0:
        raise RuntimeError("합산 기준값 0")
    growth = (latest_sum / prev_sum - 1) * 100
    return {
        "label": "Hyperscaler CAPEX YoY (%, 연간 합산 근사)",
        "value": round2(growth),
        "asOf": latest_end,
        "companies": used,
        "note": "회계연도 종료월이 회사마다 달라(MSFT 6월 vs 타사 12월) 근사치입니다.",
    }


def fetch_micron_revenue_yoy() -> dict:
    last_err = None
    for tag in REVENUE_TAGS:
        try:
            data = fetch_sec_concept(MICRON_CIK, tag)
            rows = extract_annual_facts(data)
            growth, as_of = yoy_from_annual(rows)
            return {
                "label": "Micron 매출 YoY (%, HBM 수요 근사)",
                "value": round2(growth),
                "asOf": as_of,
                "note": "HBM 개별 수요 통계가 아닌 Micron 연간 매출 성장률로 근사한 값입니다.",
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or RuntimeError("Micron 매출 조회 실패")


# ──────────────────────────────────────────────────────────────
# 4. pykrx (KOSPI EPS·배당수익률·기술적 추세, RIM 입력)
# ──────────────────────────────────────────────────────────────
RIM_TICKER = "005930"  # 삼성전자 — 필요 시 이 값만 바꾸면 대상 종목 변경 가능
RIM_ASSUMPTIONS = {"g": 0.025, "riskPremiumGrid": [0.055, 0.05, 0.045]}


def calc_index_eps_yoy(df) -> tuple[float, str]:
    """pykrx get_index_fundamental_by_date 결과(종가/PER 컬럼 보유)에서 EPS=종가/PER YoY 계산."""
    df = df[df["PER"] > 0].copy()
    if df.empty:
        raise RuntimeError("유효 PER 없음")
    df["EPS"] = df["종가"] / df["PER"]
    latest_date = df.index[-1]
    latest_eps = df["EPS"].iloc[-1]
    target = latest_date - dt.timedelta(days=365)
    base_idx = (df.index.to_series() - target).abs().idxmin()
    base_eps = df.loc[base_idx, "EPS"]
    if base_eps == 0:
        raise RuntimeError("기준 EPS 0")
    growth = (latest_eps / base_eps - 1) * 100
    return growth, str(latest_date.date())


def calc_technical_trend(closes) -> tuple[int, dict]:
    """종가 시계열 → 200일 이동평균·52주 고저 대비 위치로 -1/0/1 신호 산출."""
    if len(closes) < 60:
        raise RuntimeError("가격 데이터 부족")
    ma_window = min(200, len(closes))
    ma200 = sum(closes[-ma_window:]) / ma_window
    last = closes[-1]
    lookback = closes[-252:] if len(closes) >= 252 else closes
    hi, lo = max(lookback), min(lookback)
    near_high = (hi - last) / hi <= 0.05 if hi else False
    near_low = (last - lo) / lo <= 0.05 if lo else False
    if last > ma200 and near_high:
        signal = 1
    elif last < ma200 and near_low:
        signal = -1
    else:
        signal = 0
    detail = {"last": round2(last), "ma": round2(ma200), "high52w": round2(hi), "low52w": round2(lo)}
    return signal, detail


def fetch_krx_data() -> dict:
    from pykrx import stock

    out: dict = {}
    today = dt.date.today()
    fmt = lambda d: d.strftime("%Y%m%d")

    # -- KOSPI 지수 펀더멘털 (EPS YoY, 배당수익률) --
    start = fmt(today - dt.timedelta(days=400))
    fdf = stock.get_index_fundamental_by_date(start, fmt(today), "1001")  # 1001 = KOSPI
    eps_growth, eps_asof = calc_index_eps_yoy(fdf)
    out["kospiEps"] = {"label": "KOSPI EPS YoY (%)", "value": round2(eps_growth), "asOf": eps_asof}
    out["kospiDividendYield"] = {
        "label": "KOSPI 배당수익률 (%)",
        "value": round2(fdf["배당수익률"].iloc[-1]),
        "asOf": str(fdf.index[-1].date()),
    }

    # -- KOSPI 가격 시계열 (기술적 추세 + 차트) --
    ohlcv = stock.get_index_ohlcv_by_date(fmt(today - dt.timedelta(days=380)), fmt(today), "1001")
    closes = ohlcv["종가"].tolist()
    signal, detail = calc_technical_trend(closes)
    out["technical"] = {"label": "KOSPI 기술적 추세 (200일선/52주 고저)", "value": signal, "detail": detail}
    step = max(1, len(ohlcv) // 90)
    hist = ohlcv["종가"].iloc[::step]
    out["kospiHistory"] = [{"d": str(idx.date()), "v": round2(v)} for idx, v in hist.items()]

    # -- RIM 입력 (BPS, PER, PBR → ROE, 현재가) --
    fstart = fmt(today - dt.timedelta(days=20))
    mfund = stock.get_market_fundamental_by_date(fstart, fmt(today), RIM_TICKER)
    mfund = mfund[mfund["PER"] > 0]
    if mfund.empty:
        raise RuntimeError("RIM 종목 펀더멘털 없음")
    last_fund = mfund.iloc[-1]
    mohlcv = stock.get_market_ohlcv_by_date(fstart, fmt(today), RIM_TICKER)
    price = float(mohlcv["종가"].iloc[-1])
    roe = float(last_fund["PBR"] / last_fund["PER"]) if last_fund["PER"] else None
    out["rim"] = {
        "ticker": RIM_TICKER,
        "name": "삼성전자",
        "asOf": str(mfund.index[-1].date()),
        "price": round2(price),
        "bps": round2(float(last_fund["BPS"])),
        "per": round2(float(last_fund["PER"])),
        "pbr": round2(float(last_fund["PBR"])),
        "roe": round2(roe * 100) if roe else None,  # %
    }
    return out


# ──────────────────────────────────────────────────────────────
# 5. yfinance (미국 지수 가격 + Nasdaq(QQQ) 내재 이익성장률)
# ──────────────────────────────────────────────────────────────
US_SYMBOLS = {
    "xlk":  {"symbol": "XLK",  "label": "XLK (미국 IT)"},
    "soxx": {"symbol": "SOXX", "label": "SOXX (반도체)"},
    "sp500": {"symbol": "^GSPC", "label": "S&P 500"},
}


def implied_growth_from_pe(trailing_pe: float, forward_pe: float) -> float:
    """PER 역수 관계를 이용한 내재 이익성장률 근사: trailingPE/forwardPE - 1."""
    if not forward_pe:
        raise RuntimeError("forwardPE 없음")
    return (trailing_pe / forward_pe - 1) * 100


def fetch_us_market_data() -> dict:
    import yfinance as yf

    out: dict = {"indices": {}}
    for key, cfg in US_SYMBOLS.items():
        hist = yf.Ticker(cfg["symbol"]).history(period="1y", interval="1d")
        if hist.empty:
            raise RuntimeError(f"{cfg['symbol']} 가격 데이터 없음")
        closes = hist["Close"]
        step = max(1, len(closes) // 90)
        pts = closes.iloc[::step]
        out["indices"][key] = {
            "label": cfg["label"],
            "symbol": cfg["symbol"],
            "price": round2(float(closes.iloc[-1])),
            "asOf": str(closes.index[-1].date()),
            "return1y": round2((float(closes.iloc[-1]) / float(closes.iloc[0]) - 1) * 100),
            "history": [{"d": str(idx.date()), "v": round2(float(v))} for idx, v in pts.items()],
        }

    qqq = yf.Ticker("QQQ").info
    tpe, fpe = qqq.get("trailingPE"), qqq.get("forwardPE")
    growth = implied_growth_from_pe(tpe, fpe)
    out["nasdaqEps"] = {
        "label": "Nasdaq(QQQ) 내재 EPS 성장률 (%, trailingPE/forwardPE 근사)",
        "value": round2(growth),
        "trailingPE": round2(tpe) if tpe else None,
        "forwardPE": round2(fpe) if fpe else None,
    }
    return out


# ──────────────────────────────────────────────────────────────
# 6. RIM 적정가치 계산 (BPS·ROE·현재가는 자동, g·리스크프리미엄은 고정 가정)
# ──────────────────────────────────────────────────────────────
def compute_rim(rim_inputs: dict) -> dict:
    bps, price, roe_pct = rim_inputs["bps"], rim_inputs["price"], rim_inputs["roe"]
    rf = None  # 한국 국고채 10년으로 채움 (main에서 주입)
    return {**rim_inputs, "_bps": bps, "_price": price, "_roePct": roe_pct}


def rim_fair_value(bps: float, roe: float, rf: float, rp: float, g: float) -> float | None:
    coe = rf + rp
    if coe - g <= 0:
        return None
    return bps * (roe - g) / (coe - g)


# ──────────────────────────────────────────────────────────────
# 메인 조립
# ──────────────────────────────────────────────────────────────
def main() -> None:
    fred_key = os.environ.get("FRED_API_KEY", "").strip() or None
    ecos_key = os.environ.get("ECOS_API_KEY", "").strip() or None

    result: dict = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "fred": {},
        "ecos": {},
        "sec": {},
        "krx": {},
        "market": {},
        "rimFairValue": None,
        "errors": ERRORS,
    }

    log("== FRED 수집 ==")
    for key, cfg in FRED_SERIES.items():
        result["fred"][key] = safe(f"FRED {cfg['id']}", fetch_fred_series, key, cfg, fred_key)

    log("== ECOS 수집 ==")
    if ecos_key:
        for key, cfg in ECOS_SERIES.items():
            result["ecos"][key] = safe(f"ECOS {cfg['stat']}", fetch_ecos_series, key, cfg, ecos_key)
    else:
        ERRORS.append("ECOS_API_KEY 미설정 — 한국 기준금리/국고채/CPI 결측")
        for key in ECOS_SERIES:
            result["ecos"][key] = None

    log("== SEC EDGAR 수집 (rate-limit 준수, 다소 시간 소요) ==")
    result["sec"]["capex"] = safe("SEC Hyperscaler CAPEX", fetch_hyperscaler_capex)
    result["sec"]["hbmProxy"] = safe("SEC Micron Revenue", fetch_micron_revenue_yoy)

    log("== pykrx 수집 ==")
    krx = safe("pykrx", fetch_krx_data)
    if krx:
        result["krx"] = krx

    log("== yfinance 수집 ==")
    us = safe("yfinance", fetch_us_market_data)
    if us:
        result["market"] = us.get("indices", {})
        result["nasdaqEps"] = us.get("nasdaqEps")

    # -- RIM 적정가치 계산 (BPS/ROE/현재가 = pykrx 자동, 무위험금리 = ECOS 국고채10Y 자동) --
    rim_in = result["krx"].get("rim") if result.get("krx") else None
    rf_auto = None
    krT10 = result["ecos"].get("krTreasury10y")
    if krT10:
        rf_auto = krT10["value"] / 100
    if rim_in and rim_in.get("roe") is not None and rf_auto is not None:
        roe = rim_in["roe"] / 100
        g = RIM_ASSUMPTIONS["g"]
        fvs = [rim_fair_value(rim_in["bps"], roe, rf_auto, rp, g) for rp in RIM_ASSUMPTIONS["riskPremiumGrid"]]
        fvs = [v for v in fvs if v is not None]
        avg_fv = sum(fvs) / len(fvs) if fvs else None
        result["rimFairValue"] = {
            "ticker": rim_in["ticker"], "name": rim_in["name"], "asOf": rim_in["asOf"],
            "price": rim_in["price"], "bps": rim_in["bps"], "roePct": rim_in["roe"],
            "riskFreeRate": round2(rf_auto * 100), "g": RIM_ASSUMPTIONS["g"] * 100,
            "riskPremiumGrid": [round2(x * 100) for x in RIM_ASSUMPTIONS["riskPremiumGrid"]],
            "fairValues": [round2(v) for v in fvs],
            "avgFairValue": round2(avg_fv) if avg_fv else None,
            "upsidePct": round2((avg_fv / rim_in["price"] - 1) * 100) if avg_fv else None,
        }
    else:
        ERRORS.append("RIM 계산 불가: pykrx 또는 ECOS 국고채10Y 데이터 결측")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, ignore_nan=True) if False else \
            json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"== 완료: {out_path} (오류 {len(ERRORS)}건) ==")
    if ERRORS:
        for e in ERRORS:
            log(f" - {e}")


if __name__ == "__main__":
    main()

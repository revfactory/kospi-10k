"""KOSPI 실시간(스냅샷) 지표 수집.

GitHub Actions cron이 10분마다 실행 → data/kospi.json 갱신 → 대시보드 fetch.

데이터 소스:
- pykrx: KRX 공식 지수(KOSPI/KOSDAQ) + 투자자별 순매수 (한국 출처, 1순위)
- yfinance: ^KS11 (^KQ11), KRW=X (USD/KRW) — pykrx 실패 시 폴백 + 환율

산출:
- 지수 가격/등락/등락률, YTD/YoY, 10,000까지 % 거리
- KOSDAQ, USD/KRW
- 외국인·기관·개인 일별 순매수(억원)
- as_of(KST), source
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def fmt_date(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def walk_back_for_index(ticker: str, max_days: int = 10):
    """가장 최근 거래일 OHLCV 1행 반환 (pykrx)."""
    from pykrx import stock

    today = datetime.now(KST)
    for i in range(max_days):
        d = fmt_date(today - timedelta(days=i))
        try:
            df = stock.get_index_ohlcv_by_date(d, d, ticker)
        except Exception:
            continue
        if df is not None and not df.empty:
            return df.iloc[-1], d
    return None, None


def yoy_value(ticker: str) -> float | None:
    """1년 전 종가."""
    from pykrx import stock

    today = datetime.now(KST)
    target = today - timedelta(days=365)
    start = fmt_date(target - timedelta(days=10))
    end = fmt_date(target + timedelta(days=10))
    try:
        df = stock.get_index_ohlcv_by_date(start, end, ticker)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    # target 날짜와 가장 가까운 종가
    df = df.copy()
    df.index = df.index.tz_localize(None) if hasattr(df.index, "tz_localize") else df.index
    return float(df.iloc[-1]["종가"]) if "종가" in df.columns else None


def ytd_value(ticker: str) -> float | None:
    from pykrx import stock

    year = datetime.now(KST).year
    start = f"{year}0101"
    end = f"{year}0115"
    try:
        df = stock.get_index_ohlcv_by_date(start, end, ticker)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return float(df.iloc[0]["종가"]) if "종가" in df.columns else None


def investor_flow_kospi() -> dict | None:
    """오늘 KOSPI 투자자별 순매수 거래대금(원). pykrx 기준."""
    from pykrx import stock

    today = datetime.now(KST)
    for i in range(7):
        d = fmt_date(today - timedelta(days=i))
        try:
            df = stock.get_market_trading_value_by_date(d, d, "KOSPI")
        except Exception:
            continue
        if df is not None and not df.empty:
            row = df.iloc[-1]
            return {
                "date": d,
                "foreign_won": int(row.get("외국인합계", row.get("외국인", 0))),
                "institution_won": int(row.get("기관합계", row.get("기관", 0))),
                "individual_won": int(row.get("개인", 0)),
            }
    return None


def usdkrw() -> dict | None:
    try:
        import yfinance as yf

        t = yf.Ticker("KRW=X")
        info = t.fast_info
        price = float(info.last_price) if info.last_price else None
        prev = float(info.previous_close) if info.previous_close else None
        if price is None:
            hist = t.history(period="2d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[0]) if len(hist) > 1 else price
        change_pct = (price - prev) / prev * 100 if prev else 0.0
        return {"price": round(price, 2), "change_pct": round(change_pct, 2)}
    except Exception as e:
        print(f"[usdkrw] fallback failed: {e}", file=sys.stderr)
        return None


def yfinance_index_fallback(symbol: str) -> dict | None:
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        hist = t.history(period="5d")
        if hist.empty:
            return None
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else last
        close = float(last["Close"])
        prev_close = float(prev["Close"])
        change = close - prev_close
        return {
            "price": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(change / prev_close * 100, 2),
            "open": round(float(last["Open"]), 2),
            "high": round(float(last["High"]), 2),
            "low": round(float(last["Low"]), 2),
            "date": last.name.strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"[yfinance {symbol}] failed: {e}", file=sys.stderr)
        return None


def build_index_block(pykrx_ticker: str, yfinance_symbol: str, label: str) -> dict:
    row, d = walk_back_for_index(pykrx_ticker)
    if row is not None:
        close = float(row["종가"])
        prev = close - float(row["대비"]) if "대비" in row else close
        change = float(row["대비"]) if "대비" in row else 0.0
        change_pct = float(row["등락률"]) if "등락률" in row else (change / prev * 100 if prev else 0.0)
        block = {
            "label": label,
            "source": "pykrx",
            "price": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "open": round(float(row["시가"]), 2) if "시가" in row else None,
            "high": round(float(row["고가"]), 2) if "고가" in row else None,
            "low": round(float(row["저가"]), 2) if "저가" in row else None,
            "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
        }
    else:
        fb = yfinance_index_fallback(yfinance_symbol)
        if fb is None:
            return {"label": label, "source": "none", "error": "no data"}
        block = {"label": label, "source": "yfinance"} | fb

    # YoY/YTD (KOSPI만 의미있게)
    if pykrx_ticker == "1001":
        yoy = yoy_value(pykrx_ticker)
        ytd = ytd_value(pykrx_ticker)
        if yoy:
            block["yoy_pct"] = round((block["price"] - yoy) / yoy * 100, 2)
            block["yoy_base"] = round(yoy, 2)
        if ytd:
            block["ytd_pct"] = round((block["price"] - ytd) / ytd * 100, 2)
        block["target"] = 10000
        block["to_target_pct"] = round((10000 - block["price"]) / block["price"] * 100, 2)
        block["progress"] = round(block["price"] / 10000, 4)

    return block


def main() -> int:
    now = datetime.now(KST)
    payload: dict = {
        "as_of": now.isoformat(timespec="seconds"),
        "as_of_kst": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "kospi": build_index_block("1001", "^KS11", "KOSPI"),
        "kosdaq": build_index_block("2001", "^KQ11", "KOSDAQ"),
        "usdkrw": usdkrw(),
        "investor_flow": investor_flow_kospi(),
    }

    out = Path("data/kospi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

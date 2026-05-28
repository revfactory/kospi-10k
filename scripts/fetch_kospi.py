"""KOSPI 실시간 스냅샷 수집.

GitHub Actions cron이 10분마다 실행 → data/kospi.json 갱신 → 대시보드 fetch.

데이터 소스: Yahoo Finance (yfinance)
- ^KS11 : KOSPI 종합
- ^KQ11 : KOSDAQ
- KRW=X : USD/KRW
- 1년 전·연초 종가로 YoY/YTD, 10,000 도달까지 % 거리 산출

KRX 공식 데이터(pykrx)는 최근 인증(KRX_ID/PW)이 필요해 제외했다.
필요해지면 KRX_ID/PW를 GitHub Secret으로 추가하고 보강하면 된다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def quote(symbol: str) -> dict | None:
    """yfinance에서 단일 심볼의 최신가/등락/OHLC 반환."""
    import yfinance as yf

    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="5d", auto_adjust=False)
        if hist.empty:
            return None
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else last
        close = float(last["Close"])
        prev_close = float(prev["Close"])
        change = close - prev_close
        return {
            "source": "yfinance",
            "price": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(change / prev_close * 100, 2) if prev_close else 0.0,
            "open": round(float(last["Open"]), 2),
            "high": round(float(last["High"]), 2),
            "low": round(float(last["Low"]), 2),
            "volume": int(last["Volume"]) if "Volume" in last and last["Volume"] == last["Volume"] else None,
            "date": last.name.strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"[{symbol}] failed: {e}", file=sys.stderr)
        return None


def historical_close(symbol: str, days_back: int) -> float | None:
    """N일 전과 가까운 거래일의 종가."""
    import yfinance as yf

    try:
        t = yf.Ticker(symbol)
        end = datetime.now(KST)
        start = end - timedelta(days=days_back + 10)
        hist = t.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), auto_adjust=False)
        if hist.empty:
            return None
        target = end - timedelta(days=days_back)
        # target 이전 가장 가까운 거래일
        hist_naive = hist.copy()
        hist_naive.index = hist_naive.index.tz_localize(None) if hist_naive.index.tz else hist_naive.index
        target_naive = target.replace(tzinfo=None)
        before = hist_naive[hist_naive.index <= target_naive]
        if before.empty:
            return float(hist_naive.iloc[0]["Close"])
        return float(before.iloc[-1]["Close"])
    except Exception as e:
        print(f"[{symbol} historical {days_back}d] failed: {e}", file=sys.stderr)
        return None


def ytd_close(symbol: str) -> float | None:
    """연초 첫 거래일 종가."""
    import yfinance as yf

    try:
        t = yf.Ticker(symbol)
        year = datetime.now(KST).year
        hist = t.history(start=f"{year}-01-01", end=f"{year}-01-20", auto_adjust=False)
        if hist.empty:
            return None
        return float(hist.iloc[0]["Close"])
    except Exception as e:
        print(f"[{symbol} ytd] failed: {e}", file=sys.stderr)
        return None


def enrich_kospi(block: dict) -> dict:
    """KOSPI 전용: YoY, YTD, 10,000까지 거리 계산."""
    yoy = historical_close("^KS11", 365)
    if yoy:
        block["yoy_base"] = round(yoy, 2)
        block["yoy_pct"] = round((block["price"] - yoy) / yoy * 100, 2)
    ytd = ytd_close("^KS11")
    if ytd:
        block["ytd_base"] = round(ytd, 2)
        block["ytd_pct"] = round((block["price"] - ytd) / ytd * 100, 2)
    block["target"] = 10000
    block["to_target_pct"] = round((10000 - block["price"]) / block["price"] * 100, 2)
    block["progress"] = round(block["price"] / 10000, 4)
    return block


def main() -> int:
    now = datetime.now(KST)

    kospi = quote("^KS11")
    if kospi:
        kospi["label"] = "KOSPI"
        kospi = enrich_kospi(kospi)
    kosdaq = quote("^KQ11")
    if kosdaq:
        kosdaq["label"] = "KOSDAQ"
    krw = quote("KRW=X")
    if krw:
        krw["label"] = "USD/KRW"

    payload = {
        "as_of": now.isoformat(timespec="seconds"),
        "as_of_kst": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "kospi": kospi,
        "kosdaq": kosdaq,
        "usdkrw": krw,
    }

    out = Path("data/kospi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if kospi else 1


if __name__ == "__main__":
    sys.exit(main())

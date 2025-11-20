#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import requests
import os
from datetime import datetime, timezone
from dateutil import tz
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from statistics import mean, pstdev

console = Console()

BINANCE_BASE = "https://api.binance.com"

# ================== AYARLAR ==================

INTERVAL = "15m"
LOOKBACK = 200
REFRESH_SECONDS = 20
PRICE_DECIMALS = 4

MIN_LONG_PCT = 5.0     # Yükseliş için minimum potansiyel %
MIN_SHORT_PCT = 5.0    # Düşüş için minimum potansiyel %

LOG_FOLDER = "logs"    # log klasörü

# =============================================

last_alert_candle = {}

stats = {
    "long_signals": 0,
    "short_signals": 0,
    "cycles": 0
}

os.makedirs(LOG_FOLDER, exist_ok=True)


# ================== YARDIMCI FONKSİYONLAR ==================

def log_event(text: str):
    today = datetime.now().strftime("%Y-%m-%d")
    logfile = os.path.join(LOG_FOLDER, f"{today}.txt")
    with open(logfile, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def get_usdt_symbols():
    url = f"{BINANCE_BASE}/api/v3/exchangeInfo"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    symbols = []
    for s in data["symbols"]:
        if s["status"] != "TRADING":
            continue
        if s["quoteAsset"] != "USDT":
            continue
        sym = s["symbol"]
        if any(x in sym for x in ["UP", "DOWN", "BULL", "BEAR"]):
            continue
        symbols.append(sym)
    return symbols


def get_klines(symbol, interval, limit=500):
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    highs = [float(x[2]) for x in data]
    lows = [float(x[3]) for x in data]
    closes = [float(x[4]) for x in data]
    times = [int(x[6]) for x in data]
    return highs, lows, closes, times


def sma(values, period):
    if len(values) < period:
        return None
    return float(mean(values[-period:]))


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[-(period + 1) + i] - closes[-(period + 2) + i]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None, None
    mid = sma(closes, period)
    window = closes[-period:]
    std = pstdev(window) if len(window) >= 2 else 0.0
    upper = mid + mult * std
    lower = mid - mult * std
    return lower, mid, upper


def zscore(closes, period=20):
    if len(closes) < period:
        return None
    window = closes[-period:]
    m = mean(window)
    std = pstdev(window) if len(window) >= 2 else 0.0
    if std == 0:
        return 0.0
    return (closes[-1] - m) / std


def atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def human_time(ms):
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz.tzlocal())
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ================== GÖRÜNÜMLER ==================

def stats_panel():
    table = Table(title="📊 İstatistik Paneli", style="bright_blue")
    table.add_column("Veri", style="white")
    table.add_column("Değer", style="bold")

    table.add_row("Toplam Tarama", str(stats["cycles"]))
    table.add_row("LONG Sinyaller", str(stats["long_signals"]))
    table.add_row("SHORT Sinyaller", str(stats["short_signals"]))

    console.print(table)


def long_signal(symbol, price, target, pct, close_time):
    text = [
        f"{symbol} LONG tespit edildi",
        f"Hedef {target:.{PRICE_DECIMALS}f}",
        f"Potansiyel Yükseliş: {pct:.2f}%",
        human_time(close_time)
    ]
    console.print(Panel("\n".join(text), title="ZORA LONG", border_style="green"))
    log_event(f"[LONG] {symbol} | {pct:.2f}% | {human_time(close_time)}")


def short_signal(symbol, price, target, pct, close_time):
    text = [
        f"{symbol} SHORT tespit edildi",
        f"Hedef {target:.{PRICE_DECIMALS}f}",
        f"Potansiyel Düşüş: {pct:.2f}%",
        human_time(close_time)
    ]
    console.print(Panel("\n".join(text), title="ZORA SHORT", border_style="red"))
    log_event(f"[SHORT] {symbol} | {pct:.2f}% | {human_time(close_time)}")


# ================== ANA ÇALIŞMA DÖNGÜSÜ ==================

def main():
    console.print("[bold green]SinyalBeyBot başlatıldı[/bold green]")
    console.print("Pariteler yükleniyor...")

    try:
        symbols = get_usdt_symbols()
    except Exception as e:
        console.print(f"[red]Sembol listesi alınamadı:[/red] {e}")
        return

    console.print(f"[cyan]{len(symbols)} coin bulundu.[/cyan]")

    while True:
        stats["cycles"] += 1
        for symbol in symbols:
            try:
                highs, lows, closes, times = get_klines(symbol, INTERVAL, LOOKBACK)

                price = closes[-1]
                close_time = times[-1]

                lower, mid, upper = bollinger(closes)
                r = rsi(closes)
                z = zscore(closes)
                a = atr(highs, lows, closes)

                # Eğer herhangi bir indikatör hesaplanamadıysa geç
                if any(v is None for v in (lower, mid, upper, r, z, a)):
                    continue

                # ===== LONG =====
                long_cond = price < lower and r < 30 and z < -2
                if long_cond:
                    last = last_alert_candle.get(symbol + "_LONG")
                    if last != close_time:
                        swing_high = max(highs[-50:])
                        atr_target = price + 1.5 * a
                        target = min(swing_high, atr_target)
                        pct = (target - price) / price * 100

                        if pct >= MIN_LONG_PCT:
                            long_signal(symbol, price, target, pct, close_time)
                            stats["long_signals"] += 1
                            last_alert_candle[symbol + "_LONG"] = close_time

                # ===== SHORT =====
                short_cond = price > upper and r > 70 and z > 2
                if short_cond:
                    last = last_alert_candle.get(symbol + "_SHORT")
                    if last != close_time:
                        swing_low = min(lows[-50:])
                        atr_target = price - 1.5 * a
                        target = max(swing_low, atr_target)
                        pct = (price - target) / price * 100

                        if pct >= MIN_SHORT_PCT:
                            short_signal(symbol, price, target, pct, close_time)
                            stats["short_signals"] += 1
                            last_alert_candle[symbol + "_SHORT"] = close_time

            except Exception:
                continue

        stats_panel()
        console.print(f"Yeni tarama için {REFRESH_SECONDS} sn bekleniyor...\n")
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()

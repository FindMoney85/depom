import logging
logging.getLogger('yfinance').setLevel(logging.CRITICAL)  # gürültülü "Failed download" mesajlarını sustur

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import os

try:
    import pykap  # KAP (Kamuyu Aydınlatma Platformu) üzerinden canlı BIST hisse listesi
except ImportError:
    pykap = None

try:
    from tvDatafeed import TvDatafeed, Interval  # Gayriresmi TradingView veri erişimi
except ImportError:
    TvDatafeed = None
    Interval = None

# --- TELEGRAM AYARLARI ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# TradingView, login'siz modda kimlik bilgisi istemez ama bazı sembollerde
# veri kısıtlı/erişilemez olabilir. Bağlantı bir kere kurulup tüm taramada
# yeniden kullanılır (her sembol için yeniden bağlanmak çok yavaş olurdu).
tv_client = None
if TvDatafeed is not None:
    try:
        tv_client = TvDatafeed()  # username/password verilmezse login'siz çalışır
        print("✅ TradingView (login'siz) bağlantısı kuruldu.")
    except Exception as e:
        print(f"⚠️ TradingView bağlantısı kurulamadı, sadece yfinance kullanılacak: {e}")
        tv_client = None

# Statik yedek liste: KAP'a hiçbir şekilde erişilemezse (online=True ve
# offline/bundled veri de başarısız olursa) kullanılacak son çare listesi.
FALLBACK_SYMBOLS = [
    "AKBNK.IS", "ARCLK.IS", "ASELS.IS", "BIMAS.IS", "EREGL.IS", "FROTO.IS",
    "GARAN.IS", "HALKB.IS", "ISCTR.IS", "KCHOL.IS", "KOZAL.IS", "PETKM.IS",
    "PGSUS.IS", "SAHOL.IS", "SASA.IS", "SISE.IS", "TCELL.IS", "THYAO.IS",
    "TOASO.IS", "TUPRS.IS", "ULKER.IS", "VAKBN.IS", "YKBNK.IS",
]


def get_bist_symbols_from_kap():
    """
    Borsa İstanbul'da işlem gören hisselerin güncel listesini, Borsa
    İstanbul'un resmi kamuyu aydınlatma platformu KAP (kap.org.tr) üzerinden
    canlı olarak çeker. Statik/manuel liste tutmaya gerek kalmaz.
    """
    print("🔄 BIST hisse listesi KAP (kap.org.tr) üzerinden çekiliyor...")

    if pykap is not None:
        # 1. YOL: KAP'tan canlı (online) çekim — en güncel liste
        try:
            tickers = pykap.bist_company_list(online=True)
            if tickers and len(tickers) > 100:
                symbols = sorted({f"{t}.IS" for t in tickers})
                print(f"   ✅ {len(symbols)} hisse bulundu (KAP - canlı)")
                return symbols
        except Exception as e:
            print(f"⚠️ KAP canlı çekim hatası: {e}")

        # 2. YOL (Yedek): pykap ile birlikte gelen güncel paket verisi
        try:
            tickers = pykap.bist_company_list(online=False)
            if tickers and len(tickers) > 100:
                symbols = sorted({f"{t}.IS" for t in tickers})
                print(f"   ✅ {len(symbols)} hisse bulundu (KAP - paket verisi)")
                return symbols
        except Exception as e:
            print(f"⚠️ pykap paket verisi hatası: {e}")
    else:
        print("⚠️ pykap kütüphanesi kurulu değil (pip install pykap).")

    print("⚠️ KAP'tan liste alınamadı. Sabit yedek liste kullanılıyor...")
    return FALLBACK_SYMBOLS


def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("🚨 Hata: Telegram Token veya Chat ID bulunamadı! GitHub Secrets ayarlarını kontrol edin.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, data=payload)
        if response.status_code != 200:
            print(f"🚨 Telegram API Hatası: {response.text}")
    except Exception as e:
        print(f"Telegram mesaj hatası: {e}")


def calculate_follow_line(df, atr_period=5, bb_period=21, bb_deviation=1.0, use_atr=True):
    df['sma'] = df['close'].rolling(window=bb_period).mean()
    df['stdev'] = df['close'].rolling(window=bb_period).std(ddof=0)
    df['bb_upper'] = df['sma'] + (df['stdev'] * bb_deviation)
    df['bb_lower'] = df['sma'] - (df['stdev'] * bb_deviation)

    high_low = df['high'] - df['low']
    high_cp = (df['high'] - df['close'].shift(1)).abs()
    low_cp = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    df['atr'] = df['tr'].ewm(alpha=1/atr_period, adjust=False).mean()

    follow_line = [float('nan')] * len(df)
    i_trend = [0] * len(df)
    current_trend = 1

    for i in range(len(df)):
        if i < bb_period:
            continue

        close_val = df['close'].iloc[i]
        low_val = df['low'].iloc[i]
        high_val = df['high'].iloc[i]
        bb_upper = df['bb_upper'].iloc[i]
        bb_lower = df['bb_lower'].iloc[i]
        atr_val = df['atr'].iloc[i]
        prev_fl = follow_line[i-1]

        if close_val > bb_upper:
            current_trend = 1
        elif close_val < bb_lower:
            current_trend = -1

        current_fl = float('nan')
        if current_trend == 1:
            current_fl = (low_val - atr_val) if use_atr else low_val
            if not pd.isna(prev_fl) and current_fl < prev_fl:
                current_fl = prev_fl
        elif current_trend == -1:
            current_fl = (high_val + atr_val) if use_atr else high_val
            if not pd.isna(prev_fl) and current_fl > prev_fl:
                current_fl = prev_fl

        follow_line[i] = current_fl

        if pd.isna(follow_line[i-1]):
            i_trend[i] = current_trend
        else:
            if i_trend[i-1] == 1 and close_val < follow_line[i-1]:
                i_trend[i] = -1
            elif i_trend[i-1] == -1 and close_val > follow_line[i-1]:
                i_trend[i] = 1
            else:
                i_trend[i] = i_trend[i-1]

    df['follow_line'] = follow_line
    df['i_trend'] = i_trend
    return df


def fetch_ohlcv_tv(symbol_code, n_bars=150):
    """
    TradingView'dan (gayriresmi tvDatafeed kütüphanesi, login'siz mod)
    BIST hissesi için günlük OHLCV verisi çeker. Kimlik doğrulama
    gerektirmez ancak bazı sembollerde veri kısıtlı olabilir; böyle
    durumda None döner ve çağıran taraf yfinance'e düşer.
    """
    if tv_client is None:
        return None
    try:
        df = tv_client.get_hist(
            symbol_code, exchange='BIST', interval=Interval.in_daily, n_bars=n_bars
        )
    except Exception:
        return None

    if df is None or df.empty:
        return None

    df = df.reset_index()
    df = df.rename(columns={
        'datetime': 'timestamp', 'open': 'open', 'high': 'high',
        'low': 'low', 'close': 'close', 'volume': 'volume'
    })
    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]


def fetch_ohlcv_yf(symbol, period="1y", interval="1d"):
    """Yahoo Finance üzerinden BIST hissesi için günlük OHLCV verisi çeker."""
    data = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
    if data.empty:
        return None
    data = data.reset_index()
    # yfinance bazen MultiIndex kolon döndürebilir (tek sembolde bile)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [c[0] for c in data.columns]
    data = data.rename(columns={
        'Date': 'timestamp', 'Open': 'open', 'High': 'high',
        'Low': 'low', 'Close': 'close', 'Volume': 'volume'
    })
    return data[['timestamp', 'open', 'high', 'low', 'close', 'volume']]


def check_signals():
    bist_symbols = get_bist_symbols_from_kap()
    print(f"🔄 Tarama başlatıldı (Borsa İstanbul). Toplam Hisse Sayısı: {len(bist_symbols)}")

    buy_signals = []
    sell_signals = []
    analyzed_count = 0
    error_count = 0
    tv_used_count = 0
    yf_used_count = 0

    for symbol in bist_symbols:
        try:
            coin_name = symbol.replace('.IS', '')
            df = None
            source = None

            # 1. YOL: TradingView (login'siz)
            df = fetch_ohlcv_tv(coin_name, n_bars=150)
            if df is not None and len(df) >= 30:
                source = "TV"
                tv_used_count += 1
            else:
                # 2. YOL (Yedek): Yahoo Finance
                time.sleep(0.3)  # Yahoo Finance için nazik bekleme
                df = fetch_ohlcv_yf(symbol, period="1y", interval="1d")
                if df is not None and len(df) >= 30:
                    source = "YF"
                    yf_used_count += 1

            if df is None or len(df) < 30:
                error_count += 1
                print(f"⚠️ {symbol} için hiçbir kaynaktan yeterli veri bulunamadı.")
                continue

            df = calculate_follow_line(df)

            current_row = df.iloc[-2]
            previous_row = df.iloc[-3]

            current_trend = current_row['i_trend']
            previous_trend = previous_row['i_trend']

            close_price = round(float(current_row['close']), 2)
            analyzed_count += 1

            print(f"🔍 [{source}] {coin_name} Analiz Ediliyor... [Önceki: {previous_trend} -> Güncel: {current_trend}]")

            if previous_trend == -1 and current_trend == 1:
                buy_signals.append(f"• <b>{coin_name}</b> ({close_price} TL)")
            elif previous_trend == 1 and current_trend == -1:
                sell_signals.append(f"• <b>{coin_name}</b> ({close_price} TL)")

        except Exception as e:
            error_count += 1
            print(f"⚠️ {symbol} analiz hatası: {e}")

    # --- TOPLU RAPOR OLUŞTURMA VE GÖNDERME ---
    buy_signals.sort()
    sell_signals.sort()

    report_msg = "📊 <b>GÜNLÜK TARAMA RAPORU (Borsa İstanbul)</b>\n"
    report_msg += "───────────────────\n\n"

    report_msg += "🟢 <b>FOLLOW LINE: BUY (ALIM YAPILANLAR)</b>\n"
    if buy_signals:
        report_msg += "\n".join(buy_signals)
    else:
        report_msg += "<i>Alım sinyali üreten hisse bulunamadı.</i>"

    report_msg += "\n\n"
    report_msg += "🔴 <b>FOLLOW LINE: SELL (SATIM YAPILANLAR)</b>\n"
    if sell_signals:
        report_msg += "\n".join(sell_signals)
    else:
        report_msg += "<i>Satım sinyali üreten hisse bulunamadı.</i>"

    report_msg += f"\n\n───────────────────\n✅ Taranan Hisse: {len(bist_symbols)} | Başarıyla Analiz Edilen: {analyzed_count} | Hata Alan: {error_count}\n📡 Kaynak: TradingView {tv_used_count} | Yahoo Finance {yf_used_count}\n🔔 Yeni Al Sinyali: {len(buy_signals)} | Yeni Sat Sinyali: {len(sell_signals)}"

    send_telegram_message(report_msg)
    print("📢 Toplu rapor Telegram'a başarıyla iletildi.")


def main():
    check_signals()


if __name__ == '__main__':
    main()

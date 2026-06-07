import os
import time
import hmac
import hashlib
import requests
import pandas as pd
from datetime import datetime

# ==========================================
# SİSTEM AYARLARI
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

BB_PERIOD = 21
BB_DEVIATION = 1.0
ATR_PERIOD = 5

# ==========================================
# TELEGRAM
# ==========================================
def telegram_mesaj_gonder(mesaj):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"❌ HATA: TELEGRAM_TOKEN={'VAR' if TELEGRAM_TOKEN else 'YOK'}, CHAT_ID={'VAR' if TELEGRAM_CHAT_ID else 'YOK'}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("✅ Telegram mesajı gönderildi.")
            return True
        else:
            print(f"❌ Telegram hatası! Kod: {res.status_code}, Yanıt: {res.text}")
            return False
    except Exception as e:
        print(f"❌ Telegram bağlantı hatası: {e}")
        return False

# ==========================================
# ATR — pandas_ta olmadan manuel hesaplama
# ==========================================
def atr_hesapla(df, period=5):
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

# ==========================================
# BİNANCE TR CÜZDAN
# ==========================================
def binance_tr_imzala(params):
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    return hmac.new(
        BINANCE_SECRET_KEY.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def aktif_koinleri_getir_binance_tr():
    koin_listesi = set()

    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        print(f"❌ HATA: API_KEY={'VAR' if BINANCE_API_KEY else 'YOK'}, SECRET={'VAR' if BINANCE_SECRET_KEY else 'YOK'}")
        return []

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

    # SPOT
    try:
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 60000}
        params["signature"] = binance_tr_imzala(params)
        res = requests.get("https://api.binance.tr/api/v3/account", headers=headers, params=params, timeout=15)
        print(f"   Spot API yanıt: {res.status_code}")
        if res.status_code == 200:
            for b in res.json().get("balances", []):
                asset = b.get("asset", "").upper()
                toplam = float(b.get("free", 0)) + float(b.get("locked", 0))
                if toplam > 0.001 and asset not in ["USDT", "TRY", "FDUSD", "BNB"]:
                    koin_listesi.add(f"{asset}USDT")
                    print(f"   + Spot: {asset} ({toplam:.6f})")
        else:
            print(f"⚠️ Spot hatası: {res.text[:200]}")
    except Exception as e:
        print(f"❌ Spot tarama hatası: {e}")

    # FUTURES
    try:
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 60000}
        params["signature"] = binance_tr_imzala(params)
        res = requests.get("https://fapi.binance.com/fapi/v2/positionRisk", headers=headers, params=params, timeout=15)
        print(f"   Futures API yanıt: {res.status_code}")
        if res.status_code == 200:
            for pos in res.json():
                amt = float(pos.get("positionAmt", 0))
                symbol = pos.get("symbol", "").upper()
                if amt != 0 and symbol.endswith("USDT") and not symbol.startswith("1000"):
                    koin_listesi.add(symbol)
                    print(f"   + Futures: {symbol} ({amt})")
        else:
            print(f"⚠️ Futures çalışmıyor (Kod: {res.status_code}) — sadece spot tarandı.")
    except Exception as e:
        print(f"⚠️ Futures hatası: {e}")

    return list(koin_listesi)

# ==========================================
# VERİ & SİNYAL
# ==========================================
def verileri_cek_bybit(sembol):
    params = {"category": "linear", "symbol": sembol, "interval": "D", "limit": 60}
    try:
        res = requests.get("https://api.bybit.com/v5/market/kline", params=params, timeout=15)
        if res.status_code == 200:
            list_data = res.json().get("result", {}).get("list", [])
            if not list_data:
                print(f"   ⚠️ {sembol}: Bybit'te veri yok")
                return None
            df = pd.DataFrame(list_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df = df.iloc[::-1].reset_index(drop=True)
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        print(f"   ❌ {sembol} veri hatası: {e}")
    return None

def sinyal_kontrol_et(df):
    if df is None or len(df) < BB_PERIOD:
        return None, None

    sma = df['close'].rolling(window=BB_PERIOD).mean()
    std = df['close'].rolling(window=BB_PERIOD).std()
    df['bb_upper'] = sma + (std * BB_DEVIATION)
    df['bb_lower'] = sma - (std * BB_DEVIATION)
    df['atr'] = atr_hesapla(df, ATR_PERIOD)   # pandas_ta yerine kendi fonksiyonumuz

    bb_signal = 0
    follow_line = [0.0] * len(df)
    i_trend = [0] * len(df)

    for i in range(1, len(df)):
        close_val = df['close'].iloc[i]
        high_val  = df['high'].iloc[i]
        low_val   = df['low'].iloc[i]
        atr_val   = df['atr'].iloc[i] if not pd.isna(df['atr'].iloc[i]) else 0

        if close_val > df['bb_upper'].iloc[i]:
            bb_signal = 1
        elif close_val < df['bb_lower'].iloc[i]:
            bb_signal = -1

        prev_fl = follow_line[i - 1]
        if bb_signal == 1:
            follow_line[i] = max(low_val - atr_val, prev_fl)
        elif bb_signal == -1:
            follow_line[i] = min(high_val + atr_val, prev_fl)
        else:
            follow_line[i] = prev_fl

        if follow_line[i] > follow_line[i - 1]:
            i_trend[i] = 1
        elif follow_line[i] < follow_line[i - 1]:
            i_trend[i] = -1
        else:
            i_trend[i] = i_trend[i - 1]

    if i_trend[-2] == -1 and i_trend[-1] == 1:
        return "AL (BUY)", df['close'].iloc[-1]
    elif i_trend[-2] == 1 and i_trend[-1] == -1:
        return "SAT (SELL)", df['close'].iloc[-1]
    return None, None

# ==========================================
# ANA DÖNGÜ
# ==========================================
def ana_dongu():
    su_an = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    print(f"\n{'='*50}\nBot başlatıldı: {su_an}\n{'='*50}")

    telegram_mesaj_gonder(f"🤖 *Bot Aktif*\n⏰ {su_an}\n🔍 Cüzdan taranıyor...")

    print("\n[1] Binance TR cüzdanı taranıyor...")
    koinlerim = aktif_koinleri_getir_binance_tr()
    print(f"Bulunan koinler: {koinlerim}")

    if not koinlerim:
        telegram_mesaj_gonder(
            f"⚪ *Tarama Bitti*\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
            f"📊 Cüzdanda bakiye veya açık pozisyon bulunamadı."
        )
        return

    print(f"\n[2] {len(koinlerim)} koin analiz ediliyor...")
    toplam_sinyal = 0

    for koin in koinlerim:
        print(f"-> {koin}...")
        df = verileri_cek_bybit(koin)
        sinyal, fiyat = sinyal_kontrol_et(df)
        if sinyal:
            toplam_sinyal += 1
            mesaj = (
                f"🔔 *CÜZDAN KOİNİNDE YENİ SİNYAL*\n\n"
                f"🪙 *Koin:* {koin.replace('USDT','')}\n"
                f"📈 *Sinyal:* {sinyal}\n"
                f"💵 *Fiyat:* ${fiyat:.4f}\n"
                f"📅 *Zaman:* 1 Günlük"
            )
            telegram_mesaj_gonder(mesaj)
            print(f"   ✅ {sinyal} @ ${fiyat:.4f}")
        else:
            print(f"   — Sinyal yok")
        time.sleep(2)

    if toplam_sinyal == 0:
        telegram_mesaj_gonder(
            f"⚪ *Tarama Tamamlandı*\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
            f"📊 {len(koinlerim)} koinde yeni sinyal yok."
        )

    print(f"\n{'='*50}\nBot tamamlandı. Sinyal sayısı: {toplam_sinyal}\n{'='*50}")

if __name__ == "__main__":
    ana_dongu()

import os
import time
import hmac
import hashlib
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# ==========================================
# 1. GÜVENLİ SİSTEM AYARLARI
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY")

BB_PERIOD = 21
BB_DEVIATION = 1.0
ATR_PERIOD = 5

# ==========================================
# TELEGRAM & HATA AYIKLAMA
# ==========================================
def telegram_mesaj_gonder(mesaj):
    """Telegram'a mesaj gönderir. Başarısız olursa log yazar."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"❌ HATA: TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID eksik!")
        print(f"   TOKEN: {'VAR' if TELEGRAM_TOKEN else 'YOK'}")
        print(f"   CHAT_ID: {'VAR' if TELEGRAM_CHAT_ID else 'YOK'}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"✅ Telegram mesajı gönderildi.")
            return True
        else:
            print(f"❌ Telegram hatası! Kod: {res.status_code}, Yanıt: {res.text}")
            return False
    except Exception as e:
        print(f"❌ Telegram bağlantı hatası: {e}")
        return False

# ==========================================
# BINANCE TR CÜZDAN MOTORU
# ==========================================
def binance_tr_imzala(params, secret_key):
    """Binance TR API standartlarına göre SHA256 imzası üretir."""
    query_string = "&".join([f"{d}={v}" for d, v in params.items()])
    # DÜZELTİLDİ: hmac.new yerine hmac.new kullanımı doğruydu
    # ama secret None olduğunda çöküyor — kontrol eklendi
    return hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def aktif_koinleri_getir_binance_tr():
    """Binance TR API üzerinden cüzdanı tarar."""
    koin_listesi = set()

    # API anahtarı kontrolü
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        print("❌ HATA: BINANCE_API_KEY veya BINANCE_SECRET_KEY eksik!")
        print(f"   API KEY: {'VAR' if BINANCE_API_KEY else 'YOK'}")
        print(f"   SECRET KEY: {'VAR' if BINANCE_SECRET_KEY else 'YOK'}")
        return list(koin_listesi)

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

    # 1. AŞAMA: SPOT CÜZDAN TARAMA
    try:
        url = "https://api.binance.tr/api/v3/account"
        params = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": 60000
        }
        params["signature"] = binance_tr_imzala(params, BINANCE_SECRET_KEY)

        res = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"   Spot API yanıt kodu: {res.status_code}")

        if res.status_code == 200:
            balances = res.json().get("balances", [])
            print(f"   Toplam varlık sayısı: {len(balances)}")
            for b in balances:
                free_val = float(b.get("free", 0))
                locked_val = float(b.get("locked", 0))
                asset = b.get("asset", "").upper()
                toplam = free_val + locked_val

                if toplam > 0.001 and asset not in ["USDT", "TRY", "FDUSD", "BNB"]:
                    koin_listesi.add(f"{asset}USDT")
                    print(f"   + Spot koin bulundu: {asset} (bakiye: {toplam:.6f})")
        else:
            print(f"⚠️ Spot API hatası: {res.text[:200]}")
    except Exception as e:
        print(f"❌ Spot tarama hatası: {e}")

    # 2. AŞAMA: VADELİ İŞLEMLER (FUTURES) — DÜZELTİLDİ
    # NOT: Binance TR'de futures API çalışmayabilir, hata yönetimi eklendi
    try:
        url = "https://fapi.binance.com/fapi/v2/positionRisk"
        params = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": 60000
        }
        params["signature"] = binance_tr_imzala(params, BINANCE_SECRET_KEY)

        res = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"   Futures API yanıt kodu: {res.status_code}")

        if res.status_code == 200:
            positions = res.json()
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                symbol = pos.get("symbol", "").upper()

                if amt != 0 and symbol.endswith("USDT") and not symbol.startswith("1000"):
                    koin_listesi.add(symbol)
                    print(f"   + Futures pozisyon: {symbol} (miktar: {amt})")
        else:
            # Futures çalışmıyorsa uyarı ver ama devam et
            print(f"⚠️ Futures API çalışmıyor (Kod: {res.status_code}) — sadece spot tarandı.")
    except Exception as e:
        print(f"⚠️ Vadeli işlemler tarama hatası: {e}")

    return list(koin_listesi)

# ==========================================
# VERİ ÇEKME & STRATEJİ MOTORU
# ==========================================
def verileri_cek_bybit(sembol):
    """Bybit'ten günlük mum verisi çeker."""
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": sembol,
        "interval": "D",
        "limit": 60
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            list_data = response.json().get("result", {}).get("list", [])
            if not list_data:
                print(f"   ⚠️ {sembol}: Bybit'te veri yok (belki bu sembol Bybit'te yoktur)")
                return None
            df = pd.DataFrame(
                list_data,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover']
            )
            df = df.iloc[::-1].reset_index(drop=True)
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            return df
        else:
            print(f"   ⚠️ {sembol} Bybit hatası: {response.status_code}")
            return None
    except Exception as e:
        print(f"   ❌ {sembol} veri çekme hatası: {e}")
        return None

def sinyal_kontrol_et(df):
    """Bollinger Band + ATR tabanlı sinyal hesaplar."""
    if df is None or len(df) < BB_PERIOD:
        return None, None

    std = df['close'].rolling(window=BB_PERIOD).std()
    sma = df['close'].rolling(window=BB_PERIOD).mean()
    df['bb_upper'] = sma + (std * BB_DEVIATION)
    df['bb_lower'] = sma - (std * BB_DEVIATION)
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=ATR_PERIOD)

    bb_signal = 0
    follow_line = [0.0] * len(df)
    i_trend = [0] * len(df)

    for i in range(len(df)):
        if i == 0:
            continue
        close_val = df['close'].iloc[i]
        high_val = df['high'].iloc[i]
        low_val = df['low'].iloc[i]
        atr_val = df['atr'].iloc[i] if not pd.isna(df['atr'].iloc[i]) else 0

        if close_val > df['bb_upper'].iloc[i]:
            bb_signal = 1
        elif close_val < df['bb_lower'].iloc[i]:
            bb_signal = -1

        prev_fl = follow_line[i - 1]
        if bb_signal == 1:
            current_fl = low_val - atr_val
            follow_line[i] = max(current_fl, prev_fl)
        elif bb_signal == -1:
            current_fl = high_val + atr_val
            follow_line[i] = min(current_fl, prev_fl)
        else:
            follow_line[i] = prev_fl

        if follow_line[i] > follow_line[i - 1]:
            i_trend[i] = 1
        elif follow_line[i] < follow_line[i - 1]:
            i_trend[i] = -1
        else:
            i_trend[i] = i_trend[i - 1]

    bugun_trend = i_trend[-1]
    dun_trend = i_trend[-2]

    if dun_trend == -1 and bugun_trend == 1:
        return "AL (BUY)", df['close'].iloc[-1]
    elif dun_trend == 1 and bugun_trend == -1:
        return "SAT (SELL)", df['close'].iloc[-1]
    return None, None

# ==========================================
# ANA ÇALIŞTIRICI
# ==========================================
def ana_dongu():
    su_an = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"Bot başlatıldı: {su_an}")
    print(f"{'='*50}")

    # 1. Telegram bağlantısını test et
    print("\n[1] Telegram bağlantısı test ediliyor...")
    telegram_calisıyor = telegram_mesaj_gonder(
        f"🤖 *Bot Aktif*\n⏰ {su_an}\n🔍 Cüzdan taranıyor..."
    )
    if not telegram_calisıyor:
        print("❌ Telegram çalışmıyor! Secrets doğru girilmiş mi kontrol edin.")
        # Yine de devam et, log'dan görebiliriz

    # 2. Cüzdan tara
    print("\n[2] Binance TR cüzdanı taranıyor...")
    koinlerim = aktif_koinleri_getir_binance_tr()
    print(f"\nTarama tamamlandı. Bulunan koinler: {koinlerim}")

    if not koinlerim:
        mesaj = (
            f"⚪ *Tarama Bitti*\n"
            f"⏰ Saat: {datetime.now().strftime('%H:%M:%S')}\n"
            f"📊 *Durum:* Cüzdanda açık pozisyon veya spot bakiye algılanamadı.\n\n"
            f"_API anahtarları doğru mu? GitHub Secrets kontrol edin._"
        )
        telegram_mesaj_gonder(mesaj)
        return

    # 3. Sinyal analizi
    print(f"\n[3] {len(koinlerim)} koin Bybit'te analiz ediliyor...")
    toplam_sinyal_sayisi = 0

    for koin in koinlerim:
        print(f"\n-> {koin} analiz ediliyor...")
        df = verileri_cek_bybit(koin)
        if df is not None:
            sinyal, fiyat = sinyal_kontrol_et(df)
            if sinyal:
                toplam_sinyal_sayisi += 1
                temiz_isim = koin.replace("USDT", "")
                mesaj = (
                    f"🔔 *CÜZDAN KOİNİNDE YENİ SİNYAL* 🔔\n\n"
                    f"🪙 *Koin:* {temiz_isim}\n"
                    f"📈 *Sinyal:* {sinyal}\n"
                    f"💵 *Fiyat:* ${fiyat:.4f}\n"
                    f"📅 *Zaman:* 1 Günlük"
                )
                telegram_mesaj_gonder(mesaj)
                print(f"   ✅ Sinyal: {sinyal} @ ${fiyat:.4f}")
            else:
                print(f"   — Sinyal yok")
        time.sleep(3)

    # 4. Özet mesaj
    if toplam_sinyal_sayisi == 0:
        mesaj = (
            f"⚪ *Tarama Tamamlandı*\n"
            f"⏰ *Saat:* {datetime.now().strftime('%H:%M:%S')}\n"
            f"📊 *Durum:* Takipteki {len(koinlerim)} cüzdan koininizde "
            f"yeni sinyal değişimi yok."
        )
        telegram_mesaj_gonder(mesaj)

    print(f"\n{'='*50}")
    print(f"Bot tamamlandı. Toplam sinyal: {toplam_sinyal_sayisi}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    ana_dongu()

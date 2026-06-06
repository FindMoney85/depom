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

# Strateji Parametreleri
BB_PERIOD = 21
BB_DEVIATION = 1.0
ATR_PERIOD = 5

# ==========================================
# BINANCE CÜZDAN TARAYICI FONKSİYONLARI
# ==========================================
def binance_imzala(params):
    """Binance API istekleri için gerekli SHA256 imzasını oluşturur."""
    query_string = "&".join([f"{d}={v}" for d, v in params.items()])
    return hmac.new(BINANCE_SECRET_KEY.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

def aktif_binance_koinlerini_getir():
    """Binance Spot ve Vadeli (Futures) cüzdanındaki aktif koinleri toplar."""
    koin_listesi = set() # Çift kayıtları engellemek için küme kullanıyoruz
    timestamp = int(time.time() * 1000)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

    # 1. Aşama: SPOT CÜZDAN TARAMASI (Bakiye > 0 olanlar)
    try:
        spot_url = "https://api.binance.com/api/v3/account"
        spot_params = {"timestamp": timestamp}
        spot_params["signature"] = binance_imzala(spot_params)
        
        res = requests.get(spot_url, headers=headers, params=spot_params)
        if res.status_code == 200:
            balances = res.json().get("balances", [])
            for b in balances:
                free_val = float(b.get("free", 0))
                locked_val = float(b.get("locked", 0))
                varlik_adi = b.get("asset", "")
                
                # Toz bakiyeleri (küçük miktarları) elemek için ve USDT'yi taramamak için filtre
                if (free_val + locked_val) > 0.001 and varlik_adi not in ["USDT", "FDUSD", "BNB"]:
                    koin_listesi.add(f"{varlik_adi}USDT")
    except Exception as e:
        print(f"⚠️ Spot cüzdan okunurken hata: {e}")

    # 2. Aşama: VADELİ (FUTURES) CÜZDAN TARAMASI (Açık Pozisyonu Olanlar)
    try:
        f_url = "https://fapi.binance.com/fapi/v2/positionRisk"
        f_params = {"timestamp": timestamp}
        f_params["signature"] = binance_imzala(f_params)
        
        res = requests.get(f_url, headers=headers, params=f_params)
        if res.status_code == 200:
            positions = res.json()
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                symbol = pos.get("symbol", "")
                
                # Eğer pozisyon miktarı 0 değilse içeride açık işlem var demektir
                if amt != 0 and symbol.endswith("USDT"):
                    koin_listesi.add(symbol)
    except Exception as e:
        print(f"⚠️ Vadeli işlemler cüzdanı okunurken hata: {e}")

    return list(koin_listesi)

# ==========================================
# VERİ ÇEKME & STRATEJİ MOTORU
# ==========================================
def telegram_mesaj_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def verileri_cek_bybit(sembol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": sembol.upper(), "interval": "D", "limit": 60}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            list_data = response.json().get("result", {}).get("list", [])
            if not list_data: return None
            df = pd.DataFrame(list_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df = df.iloc[::-1].reset_index(drop=True)
            for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)
            return df
    except: return None

def sinyal_kontrol_et(df):
    if df is None or len(df) < BB_PERIOD: return None, None
    std = df['close'].rolling(window=BB_PERIOD).std()
    sma = df['close'].rolling(window=BB_PERIOD).mean()
    df['bb_upper'] = sma + (std * BB_DEVIATION)
    df['bb_lower'] = sma - (std * BB_DEVIATION)
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=ATR_PERIOD)

    bb_signal = 0
    follow_line, i_trend = [0.0] * len(df), [0] * len(df)

    for i in range(len(df)):
        if i == 0: continue
        close_val, high_val, low_val = df['close'].iloc[i], df['high'].iloc[i], df['low'].iloc[i]
        atr_val = df['atr'].iloc[i] if not pd.isna(df['atr'].iloc[i]) else 0
        
        if close_val > df['bb_upper'].iloc[i]: bb_signal = 1
        elif close_val < df['bb_lower'].iloc[i]: bb_signal = -1

        prev_fl = follow_line[i-1]
        if bb_signal == 1:
            current_fl = low_val - atr_val
            if current_fl < prev_fl: current_fl = prev_fl
            follow_line[i] = current_fl
        elif bb_signal == -1:
            current_fl = high_val + atr_val
            if current_fl > prev_fl: current_fl = prev_fl
            follow_line[i] = current_fl
        else: follow_line[i] = prev_fl

        if follow_line[i] > follow_line[i-1]: i_trend[i] = 1
        elif follow_line[i] < follow_line[i-1]: i_trend[i] = -1
        else: i_trend[i] = i_trend[i-1]

    bugun_trend, dun_trend = i_trend[-1], i_trend[-2]
    if dun_trend == -1 and bugun_trend == 1: return "AL (BUY)", df['close'].iloc[-1]
    elif dun_trend == 1 and bugun_trend == -1: return "SAT (SELL)", df['close'].iloc[-1]
    return None, None

# ==========================================
# ANA ÇALIŞTIRICI
# ==========================================
def ana_dongu():
    print("Binance cüzdanındaki koinler sorgulanıyor...")
    koinlerim = aktif_binance_koinlerini_getir()
    
    print(f"Tarama listesine alınan koinler: {koinlerim}")
    
    if not koinlerim:
        su_an = datetime.now().strftime("%H:%M:%S")
        telegram_mesaj_gonder(f"⚪ *Tarama Bitti*\n⏰ Saat: {su_an}\n📊 *Durum:* Binance cüzdanınızda açık pozisyon veya spot bakiye bulunamadı.")
        return

    toplam_sinyal_sayisi = 0
    for koin in koinlerim:
        print(f"-> {koin} kontrol ediliyor...")
        df = verileri_cek_bybit(koin)
        if df is not None:
            sinyal, fiyat = sinyal_kontrol_et(df)
            if sinyal:
                toplam_sinyal_sayisi += 1
                temiz_isim = koin.replace("USDT", "")
                mesaj = f"🔔 *CÜZDAN KOİNİNDE YENİ SİNYAL* 🔔\n\n🪙 *Koin:* {temiz_isim}\n📈 *Sinyal:* {sinyal}\n💵 *Fiyat:* ${fiyat:.4f}\n📅 *Zaman:* 1 Günlük"
                telegram_mesaj_gonder(mesaj)
        time.sleep(5)
        
    if toplam_sinyal_sayisi == 0:
        su_an = datetime.now().strftime("%H:%M:%S")
        telegram_mesaj_gonder(f"⚪ *Tarama Tamamlandı*\n⏰ *Saat:* {su_an}\n📊 *Durum:* Takipteki {len(koinlerim)} cüzdan koininizde yeni bir sinyal değişimi yok.")

if __name__ == "__main__":
    ana_dongu()

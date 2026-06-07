import os
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime

# ==========================================
# 1. GÜVENLİ SİSTEM AYARLARI
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HAFIZA_DOSYASI = "takip_listesi.txt"
BB_PERIOD = 21
BB_DEVIATION = 1.0
ATR_PERIOD = 5

# ==========================================
# TELEGRAM YARDIMCI FONKSİYONLARI
# ==========================================
def telegram_mesaj_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload)
    except: pass

def hafizadan_koinleri_oku():
    """Kayıtlı koin listesini dosyadan okur. Dosya yoksa boş liste döner."""
    if os.path.exists(HAFIZA_DOSYASI):
        with open(HAFIZA_DOSYASI, "r") as f:
            icerik = f.read().strip()
            if icerik:
                return [k.strip().upper() for k in icerik.split(",") if k.strip()]
    return []

def telegram_komutlarini_dinle():
    """Telegram'dan gelen /takip komutunu yakalar ve listeyi günceller."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        response = requests.get(url).json()
        updates = response.get("result", [])
        
        for update in reversed(updates):  # En son gelen mesajdan geriye doğru kontrol et
            mesaj = update.get("message", {})
            metin = mesaj.get("text", "")
            chat_id = str(mesaj.get("chat", {}).get("id", ""))
            
            # Sadece sizden gelen mesajları kabul etsin (Güvenlik)
            if chat_id == str(TELEGRAM_CHAT_ID):
                if metin.startswith("/takip "):
                    # Örnek gelen: "/takip btc, sei, eth" -> "BTCUSDT,SEIUSDT,ETHUSDT" haline getirilecek
                    ham_liste = metin.replace("/takip ", "").replace(" ", "").upper()
                    koinler = ham_liste.split(",")
                    
                    temiz_liste = []
                    for k in koinler:
                        if not k.endswith("USDT"):
                            k += "USDT"
                        temiz_liste.append(k)
                    
                    # Yeni listeyi dosyaya kaydet
                    yeni_icerik = ",".join(temiz_liste)
                    with open(HAFIZA_DOSYASI, "w") as f:
                        f.write(yeni_icerik)
                        
                    telegram_mesaj_gonder(f"✅ *Takip Listesi Güncellendi!*\n📊 *Yeni Listem:* {yeni_icerik.replace('USDT', '')}")
                    print(f"Hafıza güncellendi: {yeni_icerik}")
                    break # En güncel komutu işledik, döngüden çıkabiliriz
    except Exception as e:
        print(f"Telegram komut dinleme hatası: {e}")

# ==========================================
# BYBIT VERİ & STRATEJİ MOTORU
# ==========================================
def verileri_cek_bybit(sembol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category": "linear", "symbol": sembol, "interval": "D", "limit": 60}
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
    # Önce Telegram'dan yeni bir /takip komutu gelmiş mi diye kontrol et
    telegram_komutlarini_dinle()
    
    # Hafızadaki güncel koinleri yükle
    koinlerim = hafizadan_koinleri_oku()
    
    if not koinlerim:
        su_an = datetime.now().strftime("%H:%M:%S")
        print("Hafızada koin bulunamadı.")
        telegram_mesaj_gonder(f"⚪ *Tarama Durduruldu*\n⏰ Saat: {su_an}\n📊 *Durum:* Takip listeniz boş. Güncellemek için robota `/takip btc,fida,sei` gibi mesaj gönderin.")
        return

    print(f"Tarama listesindeki koinler: {koinlerim}")
    toplam_sinyal_sayisi = 0
    
    for koin in koinlerim:
        df = verileri_cek_bybit(koin)
        if df is not None:
            sinyal, fiyat = sinyal_kontrol_et(df)
            if sinyal:
                toplam_sinyal_sayisi += 1
                temiz_isim = koin.replace("USDT", "")
                mesaj = f"🔔 *YENİ SİNYAL* 🔔\n\n🪙 *Koin:* {temiz_isim}\n📈 *Sinyal:* {sinyal}\n💵 *Fiyat:* ${fiyat:.4f}\n📅 *Zaman:* 1 Günlük"
                telegram_mesaj_gonder(mesaj)
        # Her coin arası 5 saniye mola (İstediğiniz gibi)
        import time
        time.sleep(5)
        
    if toplam_sinyal_sayisi == 0:
        su_an = datetime.now().strftime("%H:%M:%S")
        telegram_mesaj_gonder(f"⚪ *Tarama Tamamlandı*\n⏰ *Saat:* {su_an}\n📊 *Durum:* Listenizdeki {len(koinlerim)} koinde yeni bir sinyal değişimi yok.")

if __name__ == "__main__":
    ana_dongu()

import time
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime  # Saat bilgisi için eklendi

# ==========================================
# 1. KULLANICI AYARLARI (BURAYI DÜZENLEYİN)
# ==========================================
TELEGRAM_TOKEN = "8014842010:AAFiOdbX6KxlmMdIhwsp7ZZdoniEP53s8hY"
TELEGRAM_CHAT_ID = "1382525386"

# Koinleri sonuna USDT gelecek şekilde yazın.
koinlerim = ["XVSUSDT", "SEIUSDT", "TIAUSDT", "GLMUSDT", "FLUXUSDT", "FIDAUSDT", "MORPHOUSDT"]

# Strateji Parametreleri
BB_PERIOD = 21
BB_DEVIATION = 1.0
ATR_PERIOD = 5


# ==========================================
# YARDIMCI FONKSİYONLAR
# ==========================================
def telegram_mesaj_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram mesajı gönderilemedi: {e}")


def verileri_cek_bybit(sembol):
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": sembol.upper(),
        "interval": "D",
        "limit": 60
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            result = data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                print(f"❓ {sembol} için Bybit üzerinde veri bulunamadı.")
                return None

            df = pd.DataFrame(list_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
            df = df.iloc[::-1].reset_index(drop=True)

            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)

            return df
        else:
            print(f"❌ {sembol} için veri çekilemedi. Hata Kodu: {response.status_code}")
            return None
    except Exception as e:
        print(f"💥 Bybit bağlantı hatası ({sembol}): {e}")
        return None


# ==========================================
# STRATEJİ HESAPLAMA MOTORU
# ==========================================
def sinyal_kontrol_et(df):
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
        if i == 0: continue

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
            if current_fl < prev_fl: current_fl = prev_fl
            follow_line[i] = current_fl
        elif bb_signal == -1:
            current_fl = high_val + atr_val
            if current_fl > prev_fl: current_fl = prev_fl
            follow_line[i] = current_fl
        else:
            follow_line[i] = prev_fl

        if follow_line[i] > follow_line[i - 1]:
            i_trend[i] = 1
        elif follow_line[i] < follow_line[i - 1]:
            i_trend[i] = -1
        else:
            i_trend[i] = i_trend[i - 1]

    df['follow_line'] = follow_line
    df['i_trend'] = i_trend

    bugun_trend = df['i_trend'].iloc[-1]
    dun_trend = df['i_trend'].iloc[-2]

    if dun_trend == -1 and bugun_trend == 1:
        return "AL (BUY)", df['close'].iloc[-1]
    elif dun_trend == 1 and bugun_trend == -1:
        return "SAT (SELL)", df['close'].iloc[-1]

    return None, None


# ==========================================
# ANA ÇALIŞTIRICI
# ==========================================
def ana_dongu():
    print("Koin tarama işlemi başlatıldı...")

    toplam_sinyal_sayisi = 0  # Sinyal durumunu takip edecek sayaç

    for koin in koinlerim:
        print(f"-> {koin.upper()} kontrol ediliyor...")
        df = verileri_cek_bybit(koin)

        if df is not None:
            sinyal, fiyat = sinyal_kontrol_et(df)

            if sinyal:
                toplam_sinyal_sayisi += 1
                temiz_isim = koin.replace("USDT", "")
                mesaj = f"🔔 *YENİ SİNYAL GELECEK* 🔔\n\n" \
                        f"🪙 *Koin:* {temiz_isim}\n" \
                        f"📈 *Sinyal:* {sinyal}\n" \
                        f"💵 *Güncel Fiyat:* ${fiyat:.4f}\n" \
                        f"📅 *Zaman Dilimi:* 1 Günlük (Daily)"

                print(f"Sinyal bulundu! Telegram'a gönderiliyor: {koin} - {sinyal}")
                telegram_mesaj_gonder(mesaj)

    # --- YENİ EKLENEN KONTROL BÖLÜMÜ ---
    # Eğer tarama bittiğinde sayaç hala 0 ise sinyal yok demektir
    if toplam_sinyal_sayisi == 0:
        su_an = datetime.now().strftime("%H:%M:%S")  # Saat-Dakika-Saniye formatı
        tarih = datetime.now().strftime("%d.%m.%Y")  # Gün.Ay.Yıl formatı

        durum_mesaji = f"⚪ *Tarama Tamamlandı*\n" \
                       f"📅 *Tarih:* {tarih}\n" \
                       f"⏰ *Saat:* {su_an}\n" \
                       f"📊 *Durum:* Herhangi bir değişiklik yok."

        print(f"Herhangi bir sinyal bulunamadı. Telegram'a 'Değişiklik Yok' mesajı gönderiliyor...")
        telegram_mesaj_gonder(durum_mesaji)

    print("Tarama başarıyla tamamlandı.")

if __name__ == "__main__":
    ana_dongu()

import ccxt
import pandas as pd
import numpy as np
import requests
import time
import os

# --- TELEGRAM AYARLARI ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# Binance Public API kullanarak kısıtlamaları aşın
exchange = ccxt.binance({'enableRateLimit': True})

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

def read_coins_from_file(filename="coinlerim.txt"):
    if not os.path.exists(filename):
        print(f"🚨 Hata: {filename} dosyası bulunamadı!")
        return []
    
    coins = []
    with open(filename, "r") as f:
        for line in f.readlines():
            coin = line.strip().upper()
            if coin:
                # Yorum satırlarını veya boşlukları atla
                if coin.startswith("#"):
                    continue
                if "/" not in coin:
                    coin = f"{coin}/USDT"
                coins.append(coin)
    return coins

def calculate_follow_line(df, atr_period=5, bb_period=21, bb_deviation=1.0, use_atr=True):
    # Bollinger Bantları Hesaplaması
    df['sma'] = df['close'].rolling(window=bb_period).mean()
    df['stdev'] = df['close'].rolling(window=bb_period).std(ddof=0)
    df['bb_upper'] = df['sma'] + (df['stdev'] * bb_deviation)
    df['bb_lower'] = df['sma'] - (df['stdev'] * bb_deviation)
    
    # ATR Hesaplaması
    high_low = df['high'] - df['low']
    high_cp = (df['high'] - df['close'].shift(1)).abs()
    low_cp = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    df['atr'] = df['tr'].ewm(alpha=1/atr_period, adjust=False).mean()
    
    follow_line = [float('nan')] * len(df)
    i_trend = [0] * len(df)
    
    # Trend takibi için yardımcı değişkenler
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
        
        # Orijinal indikatör mantığı: Fiyat banta göre trend yönünü belirler
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
        
        # Trend geçiş tetiklenmesi: Fiyatın Follow Line'ı kırması kontrolü
        if pd.isna(follow_line[i-1]):
            i_trend[i] = current_trend
        else:
            # Eğer trend yukarıysa ve fiyat çizginin altına sarkarsa trend döner
            if i_trend[i-1] == 1 and close_val < follow_line[i-1]:
                i_trend[i] = -1
            # Eğer trend aşağıysa ve fiyat çizginin üstüne çıkarsa trend döner
            elif i_trend[i-1] == -1 and close_val > follow_line[i-1]:
                i_trend[i] = 1
            else:
                i_trend[i] = i_trend[i-1]
                
    df['follow_line'] = follow_line
    df['i_trend'] = i_trend
    return df

def check_signals():
    my_symbols = read_coins_from_file()
    if not my_symbols:
        print("⚠️ Taranacak coin bulunamadı. Liste boş veya dosya eksik.")
        return

    print(f"🔄 Tarama başlatıldı (Binance Servisi). Toplam Coin Sayısı: {len(my_symbols)}")
    
    # Binance borsasındaki aktif sembolleri kontrol etmek için yükle
    try:
        exchange.load_markets()
    except Exception as e:
        print(f"🚨 Binance piyasa verileri yüklenemedi: {e}")
        return
        
    for symbol in my_symbols:
        if symbol not in exchange.markets:
            print(f"⚠️ {symbol} Binance üzerinde bulunamadı, atlanıyor...")
            continue
            
        try:
            # 150 gün yerine indikatör otursun diye limit 200 yapıldı
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=200)
            if len(ohlcv) < 30:
                print(f"⚠️ {symbol} için yeterli geçmiş veri yok.")
                continue
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            df = calculate_follow_line(df)
            
            # Güncel ve bir önceki tamamlanmış günün satırları
            current_row = df.iloc[-2]
            previous_row = df.iloc[-3]
            
            current_trend = current_row['i_trend']
            previous_trend = previous_row['i_trend']
            candle_time = current_row['timestamp'].strftime('%Y-%m-%d')
            
            print(f"🔍 {symbol} Analiz Ediliyor... [Dün: {previous_trend} -> Bugün: {current_trend}]")
            
            signal = None
            if previous_trend == -1 and current_trend == 1:
                signal = "🟢 <b>FOLLOW LINE: BUY (AL)</b>"
            elif previous_trend == 1 and current_trend == -1:
                signal = "🔴 <b>FOLLOW LINE: SELL (SAT)</b>"
                
            if signal:
                msg = f"🚨 <b>{symbol} - Günlük Grafik</b>\n\nSinyal: {signal}\nKapanış Fiyatı: {current_row['close']}\nSinyal Günü: {candle_time}"
                send_telegram_message(msg)
                print(f"🔔 Sinyal gönderildi: {symbol} -> {signal}")
                    
            time.sleep(0.5) # Rate limit aşım koruması
            
        except Exception as e:
            print(f"❌ {symbol} taranırken hata oluştu: {e}")

def main():
    check_signals()

if __name__ == '__main__':
    main()

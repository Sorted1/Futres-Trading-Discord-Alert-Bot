import aiohttp
import urllib.parse
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict

async def fetch_symbol_price(session: aiohttp.ClientSession, symbol: str):
    """
    Fetch current price data for a symbol from Yahoo Finance.
    Returns: (symbol, data_dict) or (symbol, None) on error
    """
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?interval=1m&range=1d"
    
    try:
        async with session.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            
            if resp.status != 200:
                print(f"[fetcher] HTTP {resp.status} for {symbol}")
                return symbol, None
                
            data = await resp.json()
    except aiohttp.ClientError as e:
        print(f"[fetcher] Network error for {symbol}: {e}")
        return symbol, None
    except Exception as e:
        print(f"[fetcher] Error fetching {symbol}: {e}")
        return symbol, None

    result = data.get("chart", {}).get("result")
    if not result:
        print(f"[fetcher] No data in response for {symbol}")
        return symbol, None

    try:
        meta = result[0].get("meta", {})
        timestamps = result[0].get("timestamp", [])
        indicators = result[0].get("indicators", {})
        quote = indicators.get("quote", [{}])[0]
        
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        opens = quote.get("open", [])
        
        # Get the latest valid price
        latest_price = None
        latest_volume = 0
        latest_high = None
        latest_low = None
        latest_open = None
        latest_time = None
        
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] is not None:
                latest_price = closes[i]
                latest_volume = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
                latest_high = highs[i] if i < len(highs) and highs[i] is not None else latest_price
                latest_low = lows[i] if i < len(lows) and lows[i] is not None else latest_price
                latest_open = opens[i] if i < len(opens) and opens[i] is not None else latest_price
                latest_time = timestamps[i] if i < len(timestamps) else None
                break
        
        if latest_price is None:
            # Try regularMarketPrice from meta
            latest_price = meta.get("regularMarketPrice")
            if latest_price is None:
                return symbol, None
        
        print(f"[fetcher] {symbol} price: {latest_price}")
        
        return symbol, {
            "price": latest_price,
            "volume": latest_volume,
            "high": latest_high or latest_price,
            "low": latest_low or latest_price,
            "open": latest_open or latest_price,
            "timestamp": latest_time or int(time.time()),
            "previous_close": meta.get("previousClose"),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("exchangeName", "Unknown")
        }
        
    except Exception as e:
        print(f"[fetcher] Parsing error for {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return symbol, None

async def fetch_historical_data(
    session: aiohttp.ClientSession, 
    symbol: str, 
    period: str = "5d",
    interval: str = "5m"
) -> Optional[List[Dict]]:
    """Fetch historical OHLCV data for technical indicator calculation."""
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
    params = {"interval": interval, "range": period, "includeAdjustedClose": "true"}
    
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    
    try:
        async with session.get(full_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as e:
        print(f"[fetcher] Error fetching historical data for {symbol}: {e}")
        return None
    
    result = data.get("chart", {}).get("result")
    if not result:
        return None
    
    try:
        timestamps = result[0].get("timestamp", [])
        indicators = result[0].get("indicators", {})
        quote = indicators.get("quote", [{}])[0]
        
        candles = []
        for i in range(len(timestamps)):
            close = quote.get("close", [])[i] if i < len(quote.get("close", [])) else None
            if close is None:
                continue
            
            candle = {
                "timestamp": timestamps[i],
                "open": quote.get("open", [])[i] if i < len(quote.get("open", [])) else close,
                "high": quote.get("high", [])[i] if i < len(quote.get("high", [])) else close,
                "low": quote.get("low", [])[i] if i < len(quote.get("low", [])) else close,
                "close": close,
                "volume": int(quote.get("volume", [])[i]) if i < len(quote.get("volume", [])) and quote.get("volume", [])[i] else 0
            }
            candles.append(candle)
        
        print(f"[fetcher] Loaded {len(candles)} historical candles for {symbol}")
        return candles
        
    except Exception as e:
        print(f"[fetcher] Error parsing historical data for {symbol}: {e}")
        return None

async def fetch_multiple_symbols(session: aiohttp.ClientSession, symbols: List[str]) -> Dict[str, Optional[Dict]]:
    """
    Fetch prices for multiple symbols concurrently.
    Returns dict mapping symbol to data (or None)
    """
    import asyncio
    
    tasks = [fetch_symbol_price(session, sym) for sym in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    output = {}
    for (symbol, data), sym in zip(results, symbols):
        if isinstance(symbol, Exception):
            print(f"[fetcher] Exception for {sym}: {symbol}")
            output[sym] = None
        else:
            output[sym] = data
    
    return output
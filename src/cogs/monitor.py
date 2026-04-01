import discord
from discord.ext import tasks, commands
import aiohttp
import numpy as np
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import asyncio

from utils.config import (
    SYMBOLS, UPDATE_INTERVAL, MONITOR_ALERT_CHANNEL, 
    DEFAULT_ALERT_THRESHOLDS, ALERT_COOLDOWNS, COLORS,
    CONTRACT_SPECS, EMBED_COLOR
)
from utils.fetcher import fetch_symbol_price, fetch_historical_data

@dataclass
class PriceLevel:
    """Represents a support/resistance level with strength scoring"""
    price: float
    touches: int = 1
    strength: float = 0.2
    last_touch: datetime = field(default_factory=datetime.now)
    level_type: str = "support"

@dataclass
class Alert:
    """Structured trading alert with full context"""
    symbol: str
    alert_type: str
    message: str
    severity: str
    timestamp: datetime
    price: float
    context: Dict[str, Any] = field(default_factory=dict)
    suggested_action: Optional[str] = None

class SymbolState:
    """Complete state management for a trading symbol"""
    def __init__(self, symbol: str, lookback: int = 100):
        self.symbol = symbol
        self.lookback = lookback
        
        # Price history
        self.prices: deque = deque(maxlen=lookback)
        self.volumes: deque = deque(maxlen=lookback)
        self.highs: deque = deque(maxlen=lookback)
        self.lows: deque = deque(maxlen=lookback)
        self.opens: deque = deque(maxlen=lookback)
        self.timestamps: deque = deque(maxlen=lookback)
        
        # Technical indicators
        self.vwap: Optional[float] = None
        self.ema_9: Optional[float] = None
        self.ema_20: Optional[float] = None
        self.ema_50: Optional[float] = None
        self.rsi: Optional[float] = None
        self.atr: Optional[float] = None
        self.atr_pct: Optional[float] = None
        self.bollinger_upper: Optional[float] = None
        self.bollinger_lower: Optional[float] = None
        self.bb_width: Optional[float] = None
        self.volume_sma: Optional[float] = None
        
        # Market structure
        self.support_levels: List[PriceLevel] = []
        self.resistance_levels: List[PriceLevel] = []
        
        # Trend analysis
        self.trend: str = "neutral"
        self.trend_strength: float = 0.0
        self.position_vs_vwap: Optional[str] = None
        
        # Volatility regime
        self.volatility_regime: str = "normal"
        
        # Alert management
        self.last_alert_time: Dict[str, datetime] = {}
        self.alert_history: deque = deque(maxlen=50)
        
        # Session tracking
        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.session_volume: float = 0.0
        
        # Divergence detection
        self.rsi_history: deque = deque(maxlen=20)

class MonitorCog(commands.Cog):
    """Advanced futures trading monitor with smart alert aggregation"""
    
    def __init__(self, bot):
        self.bot = bot
        self.symbols_config = SYMBOLS if isinstance(SYMBOLS, dict) else {}
        self.symbol_list = list(self.symbols_config.keys()) if self.symbols_config else ["NQ=F", "ES=F", "GC=F"]
        
        self.states: Dict[str, SymbolState] = {
            symbol: SymbolState(symbol) for symbol in self.symbol_list
        }
        self.session: Optional[aiohttp.ClientSession] = None
        self.dashboard_msg: Optional[discord.Message] = None
        self.alert_channel: Optional[discord.TextChannel] = None
        self.is_initialized = False
        
        # ALERT AGGREGATION SYSTEM
        self.pending_alerts: List[Alert] = []  # Buffer for batching
        self.alert_batch_task: Optional[asyncio.Task] = None
        self.last_batch_time: datetime = datetime.now()
        self.BATCH_INTERVAL = 30  # Send aggregated alerts every 30 seconds
        self.MAX_ALERTS_PER_BATCH = 5  # Max alerts in one embed
        
        # Start initialization
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        """Initialize session and load historical data"""
        await self.bot.wait_until_ready()
        
        # Setup session with connection pooling
        connector = aiohttp.TCPConnector(
            limit=20,
            limit_per_host=10,
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        
        # Get alert channel
        self.alert_channel = self.bot.get_channel(MONITOR_ALERT_CHANNEL)
        if not self.alert_channel:
            print(f"[monitor] WARNING: Alert channel {MONITOR_ALERT_CHANNEL} not found")
        
        print("[monitor] Loading historical data for indicator warmup...")
        await self._load_historical_data()
        
        self.is_initialized = True
        self.monitor_loop.start()
        self.alert_batch_loop.start()  # Start the alert batching loop
        print("[monitor] Initialization complete. Monitoring started.")

    async def _load_historical_data(self):
        """Pre-load historical data for accurate indicators"""
        for symbol in self.symbol_list:
            try:
                hist_data = await fetch_historical_data(
                    self.session, symbol, period="5d", interval="5m"
                )
                if hist_data and len(hist_data) > 20:
                    state = self.states[symbol]
                    for candle in hist_data:
                        state.prices.append(candle['close'])
                        state.volumes.append(candle.get('volume', 0))
                        state.highs.append(candle['high'])
                        state.lows.append(candle['low'])
                        state.opens.append(candle['open'])
                        state.timestamps.append(
                            datetime.fromtimestamp(candle['timestamp'])
                        )
                    print(f"[monitor] Loaded {len(hist_data)} candles for {symbol}")
            except Exception as e:
                print(f"[monitor] Error loading history for {symbol}: {e}")

    async def cog_unload(self):
        """Cleanup on cog unload"""
        self.monitor_loop.cancel()
        self.alert_batch_loop.cancel()
        if self.session:
            await self.session.close()
            await asyncio.sleep(0.25)

    def calculate_all_indicators(self, state: SymbolState):
        """Calculate complete technical indicator suite"""
        if len(state.prices) < 20:
            return
        
        prices = np.array(state.prices)
        volumes = np.array(state.volumes) if state.volumes else np.ones(len(prices))
        highs = np.array(state.highs) if state.highs else prices
        lows = np.array(state.lows) if state.lows else prices
        
        # VWAP
        if len(volumes) > 0 and np.sum(volumes) > 0:
            state.vwap = np.sum(prices * volumes) / np.sum(volumes)
        
        # EMAs
        state.ema_9 = self._calculate_ema(prices, 9)
        state.ema_20 = self._calculate_ema(prices, 20)
        state.ema_50 = self._calculate_ema(prices, 50) if len(prices) >= 50 else None
        
        # RSI
        state.rsi = self._calculate_rsi(prices, 14)
        if state.rsi:
            state.rsi_history.append(state.rsi)
        
        # ATR and volatility
        state.atr = self._calculate_atr(highs, lows, prices, 14)
        if state.atr and len(prices) > 0:
            state.atr_pct = (state.atr / prices[-1]) * 100
        
        # Bollinger Bands
        if len(prices) >= 20:
            sma_20 = np.mean(prices[-20:])
            std_20 = np.std(prices[-20:])
            state.bollinger_upper = sma_20 + (2 * std_20)
            state.bollinger_lower = sma_20 - (2 * std_20)
            state.bb_width = ((state.bollinger_upper - state.bollinger_lower) / sma_20) * 100
        
        # Volume SMA
        if len(volumes) >= 20:
            state.volume_sma = np.mean(volumes[-20:])
        
        # Trend determination
        self._determine_trend(state, prices)
        
        # Volatility regime
        self._determine_volatility_regime(state, prices)
        
        # Support/Resistance
        self._detect_support_resistance(state, prices, highs, lows)

    def _calculate_ema(self, prices: np.ndarray, period: int) -> float:
        """Calculate EMA"""
        if len(prices) < period:
            return prices[-1]
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()
        return np.convolve(prices[-period:], weights, mode='valid')[0]

    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> Optional[float]:
        """Calculate RSI with smoothing"""
        if len(prices) < period + 1:
            return None
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calculate_atr(self, highs: np.ndarray, lows: np.ndarray, 
                     closes: np.ndarray, period: int = 14) -> Optional[float]:
        """Calculate Average True Range"""
        if len(highs) < period + 1:
            return None
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        tr = np.maximum(np.maximum(tr1, tr2), tr3)
        return np.mean(tr[-period:])

    def _determine_trend(self, state: SymbolState, prices: np.ndarray):
        """Determine trend using EMA alignment"""
        if state.ema_20 and state.ema_50:
            if state.ema_20 > state.ema_50:
                state.trend = "bullish"
                state.trend_strength = min(1.0, (state.ema_20 - state.ema_50) / state.ema_50 * 100)
            elif state.ema_20 < state.ema_50:
                state.trend = "bearish"
                state.trend_strength = min(1.0, (state.ema_50 - state.ema_20) / state.ema_50 * 100)
            else:
                state.trend = "neutral"
                state.trend_strength = 0.0
        
        if state.vwap:
            current = prices[-1]
            state.position_vs_vwap = "above" if current > state.vwap else "below"

    def _determine_volatility_regime(self, state: SymbolState, prices: np.ndarray):
        """Classify volatility regime based on ATR"""
        if not state.atr_pct:
            return
        
        if state.atr_pct < 0.1:
            state.volatility_regime = "low"
        elif state.atr_pct < 0.3:
            state.volatility_regime = "normal"
        elif state.atr_pct < 0.6:
            state.volatility_regime = "high"
        else:
            state.volatility_regime = "extreme"

    def _detect_support_resistance(self, state: SymbolState, prices: np.ndarray, 
                                   highs: np.ndarray, lows: np.ndarray):
        """Detect support and resistance levels using pivot points"""
        if len(prices) < 10:
            return
        
        pivot_highs = []
        pivot_lows = []
        
        for i in range(3, len(prices) - 3):
            if highs[i] == max(highs[i-3:i+4]):
                pivot_highs.append((i, float(highs[i])))
            if lows[i] == min(lows[i-3:i+4]):
                pivot_lows.append((i, float(lows[i])))
        
        def cluster_pivots(pivots, level_type):
            if not pivots:
                return []
            levels = []
            for idx, price in pivots:
                found = False
                for level in levels:
                    if abs(level.price - price) / price < 0.0015:
                        level.touches += 1
                        level.strength = min(1.0, level.touches / 5 + 0.2)
                        level.last_touch = datetime.now()
                        found = True
                        break
                if not found and len(levels) < 5:
                    levels.append(PriceLevel(
                        price=price,
                        touches=1,
                        strength=0.2,
                        last_touch=datetime.now(),
                        level_type=level_type
                    ))
            return sorted(levels, key=lambda x: x.strength, reverse=True)
        
        state.resistance_levels = cluster_pivots(pivot_highs, 'resistance')
        state.support_levels = cluster_pivots(pivot_lows, 'support')

    def detect_divergence(self, state: SymbolState) -> Optional[Tuple[str, str]]:
        """Detect RSI divergence patterns"""
        if len(state.rsi_history) < 10 or len(state.prices) < 10:
            return None
        
        prices = list(state.prices)[-10:]
        rsi_vals = list(state.rsi_history)[-10:]
        
        price_highs = [(i, prices[i]) for i in range(1, len(prices)-1) 
                       if prices[i] > prices[i-1] and prices[i] > prices[i+1]]
        price_lows = [(i, prices[i]) for i in range(1, len(prices)-1) 
                      if prices[i] < prices[i-1] and prices[i] < prices[i+1]]
        
        if len(price_highs) >= 2:
            recent_high = price_highs[-1]
            prev_high = price_highs[-2]
            if recent_high[1] > prev_high[1]:
                recent_rsi = rsi_vals[recent_high[0]]
                prev_rsi = rsi_vals[prev_high[0]]
                if recent_rsi < prev_rsi - 3:
                    return ("Bearish", "strong" if recent_rsi > 60 else "weak")
        
        if len(price_lows) >= 2:
            recent_low = price_lows[-1]
            prev_low = price_lows[-2]
            if recent_low[1] < prev_low[1]:
                recent_rsi = rsi_vals[recent_low[0]]
                prev_rsi = rsi_vals[prev_low[0]]
                if recent_rsi > prev_rsi + 3:
                    return ("Bullish", "strong" if recent_rsi < 40 else "weak")
        
        return None

    def check_all_alerts(self, state: SymbolState, current_price: float, 
                         current_volume: float) -> List[Alert]:
        """Generate comprehensive trading alerts"""
        alerts = []
        now = datetime.now()
        symbol_config = CONTRACT_SPECS.get(state.symbol, {})
        
        # Only check for most important alerts to reduce spam
        # Priority: VWAP Cross > Trend Change > Extreme RSI > BB Breakout > Volume Spike
        
        # 1. VWAP Cross (High Priority)
        if state.vwap:
            prev_pos = state.position_vs_vwap
            curr_pos = "above" if current_price > state.vwap else "below"
            deviation = abs(current_price - state.vwap) / state.vwap * 100
            
            if prev_pos and prev_pos != curr_pos:
                if self._can_alert(state, 'vwap_cross', now):
                    direction = "BULLISH" if curr_pos == "above" else "BEARISH"
                    severity = "critical" if deviation > 0.5 else "warning"
                    atr_stop = state.atr * 1.5 if state.atr else current_price * 0.002
                    stop = state.vwap - atr_stop if curr_pos == "above" else state.vwap + atr_stop
                    
                    alerts.append(Alert(
                        symbol=state.symbol,
                        alert_type='vwap_cross',
                        message=f"🎯 VWAP CROSS - {direction} ({deviation:.2f}%)",
                        severity=severity,
                        timestamp=now,
                        price=current_price,
                        context={'vwap': round(state.vwap, 2), 'stop': round(stop, 2)},
                        suggested_action=f"Entry {direction.lower()}, stop {stop:.2f}"
                    ))
        
        # 2. RSI Divergence (Medium Priority)
        divergence = self.detect_divergence(state)
        if divergence:
            div_type, strength = divergence
            if strength == 'strong' and self._can_alert(state, f'rsi_divergence_{div_type}', now, cooldown=1800):
                alerts.append(Alert(
                    symbol=state.symbol,
                    alert_type='rsi_divergence',
                    message=f"⚠️ RSI {div_type} Divergence ({strength})",
                    severity='warning',
                    timestamp=now,
                    price=current_price,
                    context={'rsi': round(state.rsi, 1) if state.rsi else None},
                    suggested_action=f"Watch for {div_type.lower()} reversal"
                ))
        
        # 3. Extreme RSI only (skip regular overbought/oversold to reduce spam)
        if state.rsi:
            if state.rsi > DEFAULT_ALERT_THRESHOLDS['rsi_extreme_overbought']:
                if self._can_alert(state, 'rsi_extreme_overbought', now, cooldown=1800):
                    alerts.append(Alert(
                        symbol=state.symbol,
                        alert_type='rsi_extreme_overbought',
                        message=f"🔴 RSI EXTREME OVERBOUGHT ({state.rsi:.1f})",
                        severity='critical',
                        timestamp=now,
                        price=current_price,
                        context={'rsi': round(state.rsi, 1)},
                        suggested_action="Consider profit taking"
                    ))
            elif state.rsi < DEFAULT_ALERT_THRESHOLDS['rsi_extreme_oversold']:
                if self._can_alert(state, 'rsi_extreme_oversold', now, cooldown=1800):
                    alerts.append(Alert(
                        symbol=state.symbol,
                        alert_type='rsi_extreme_oversold',
                        message=f"🟢 RSI EXTREME OVERSOLD ({state.rsi:.1f})",
                        severity='critical',
                        timestamp=now,
                        price=current_price,
                        context={'rsi': round(state.rsi, 1)},
                        suggested_action="Consider bounce entry"
                    ))
        
        # 4. Strong S/R levels only (80%+ strength, within 0.1%)
        sr_threshold = DEFAULT_ALERT_THRESHOLDS['sr_proximity_pct'] / 100
        
        for level in state.support_levels[:2]:
            if level.strength >= 0.8:
                distance = abs(current_price - level.price) / level.price
                if distance < sr_threshold:
                    if self._can_alert(state, f'support_{level.price:.0f}', now, cooldown=1800):
                        alerts.append(Alert(
                            symbol=state.symbol,
                            alert_type='support_test',
                            message=f"🛡️ Strong Support {level.price:.2f} (S:{level.strength:.0%})",
                            severity='info',
                            timestamp=now,
                            price=current_price,
                            context={'level': round(level.price, 2), 'strength': f"{level.strength:.0%}"},
                            suggested_action="Watch for bounce"
                        ))
        
        for level in state.resistance_levels[:2]:
            if level.strength >= 0.8:
                distance = abs(current_price - level.price) / level.price
                if distance < sr_threshold:
                    if self._can_alert(state, f'resistance_{level.price:.0f}', now, cooldown=1800):
                        alerts.append(Alert(
                            symbol=state.symbol,
                            alert_type='resistance_test',
                            message=f"🧱 Strong Resistance {level.price:.2f} (S:{level.strength:.0%})",
                            severity='info',
                            timestamp=now,
                            price=current_price,
                            context={'level': round(level.price, 2), 'strength': f"{level.strength:.0%}"},
                            suggested_action="Watch for breakout"
                        ))
        
        # 5. Bollinger Band Squeeze (only if very tight < 0.3%)
        if state.bb_width and state.bb_width < 0.3:
            if self._can_alert(state, 'bb_squeeze', now, cooldown=3600):  # 1 hour cooldown
                alerts.append(Alert(
                    symbol=state.symbol,
                    alert_type='bb_squeeze',
                    message=f"🌀 BB Squeeze ({state.bb_width:.2f}%) - Breakout Soon",
                    severity='warning',
                    timestamp=now,
                    price=current_price,
                    context={'width': f"{state.bb_width:.2f}%"},
                    suggested_action="Prepare for directional move"
                ))
        
        # 6. BB Breakout (only on close outside bands, not touch)
        if state.bollinger_upper and state.bollinger_lower:
            if current_price > state.bollinger_upper * 1.001:  # 0.1% above
                if self._can_alert(state, 'bb_breakout', now):
                    alerts.append(Alert(
                        symbol=state.symbol,
                        alert_type='bb_breakout',
                        message=f"🚀 BB Breakout Upper",
                        severity='critical',
                        timestamp=now,
                        price=current_price,
                        context={'band': round(state.bollinger_upper, 2)},
                        suggested_action="Momentum continuation"
                    ))
            elif current_price < state.bollinger_lower * 0.999:  # 0.1% below
                if self._can_alert(state, 'bb_breakdown', now):
                    alerts.append(Alert(
                        symbol=state.symbol,
                        alert_type='bb_breakdown',
                        message=f"🔻 BB Breakdown Lower",
                        severity='critical',
                        timestamp=now,
                        price=current_price,
                        context={'band': round(state.bollinger_lower, 2)},
                        suggested_action="Short continuation"
                    ))
        
        # 7. Volume Spike (4x+ average only)
        if state.volume_sma and current_volume > state.volume_sma * 4:
            if self._can_alert(state, 'volume_spike', now):
                ratio = current_volume / state.volume_sma
                alerts.append(Alert(
                    symbol=state.symbol,
                    alert_type='volume_spike',
                    message=f"📢 Volume Spike ({ratio:.1f}x avg)",
                    severity='warning',
                    timestamp=now,
                    price=current_price,
                    context={'ratio': f"{ratio:.1f}x"},
                    suggested_action="Confirm with price action"
                ))
        
        # 8. Extreme Volatility only
        if state.volatility_regime == "extreme":
            if self._can_alert(state, 'extreme_volatility', now, cooldown=1800):
                alerts.append(Alert(
                    symbol=state.symbol,
                    alert_type='extreme_volatility',
                    message=f"⚡ EXTREME VOLATILITY (ATR: {state.atr_pct:.2f}%)",
                    severity='critical',
                    timestamp=now,
                    price=current_price,
                    context={'atr': f"{state.atr_pct:.2f}%"},
                    suggested_action="Widen stops, reduce size"
                ))
        
        # 9. Trend Change (EMA Cross)
        if state.ema_20 and state.ema_50:
            prev_trend = state.trend
            if state.ema_20 > state.ema_50:
                curr_trend = "bullish"
            elif state.ema_20 < state.ema_50:
                curr_trend = "bearish"
            else:
                curr_trend = "neutral"
            
            if prev_trend != curr_trend and prev_trend != "neutral":
                if self._can_alert(state, 'trend_change', now):
                    emoji = "📈" if curr_trend == "bullish" else "📉"
                    alerts.append(Alert(
                        symbol=state.symbol,
                        alert_type='trend_change',
                        message=f"{emoji} Trend Change to {curr_trend.upper()}",
                        severity='warning',
                        timestamp=now,
                        price=current_price,
                        context={'trend': curr_trend},
                        suggested_action=f"Trade with {curr_trend} trend"
                    ))
        
        return alerts

    def _can_alert(self, state: SymbolState, alert_key: str, now: datetime, 
                   cooldown: int = None) -> bool:
        """Check if alert can fire (cooldown expired)"""
        cooldown = cooldown or ALERT_COOLDOWNS.get(alert_key, 300)
        last_time = state.last_alert_time.get(alert_key)
        
        if last_time is None or (now - last_time).total_seconds() > cooldown:
            state.last_alert_time[alert_key] = now
            return True
        return False

    @tasks.loop(seconds=UPDATE_INTERVAL)
    async def monitor_loop(self):
        """Main monitoring loop - only collects alerts, doesn't send"""
        if not self.is_initialized:
            return
        
        try:
            channel = self.alert_channel or self.bot.get_channel(MONITOR_ALERT_CHANNEL)
            if not channel:
                print(f"[monitor] Channel {MONITOR_ALERT_CHANNEL} not found")
                return
            
            embed = discord.Embed(
                title="📊 Futures Trading Dashboard",
                description=f"Real-time Technical Analysis | {datetime.now().strftime('%H:%M:%S')}",
                color=EMBED_COLOR
            )
            
            new_alerts = []
            
            for symbol in self.symbol_list:
                try:
                    symbol_config = self.symbols_config.get(symbol, {})
                    
                    _, data = await fetch_symbol_price(self.session, symbol)
                    if not data:
                        embed.add_field(
                            name=f"❌ {symbol}",
                            value="Data unavailable",
                            inline=True
                        )
                        continue
                    
                    price = data['price']
                    volume = data.get('volume', 0)
                    state = self.states[symbol]
                    
                    # Update state
                    state.prices.append(price)
                    state.volumes.append(volume)
                    state.highs.append(data.get('high', price))
                    state.lows.append(data.get('low', price))
                    state.opens.append(data.get('open', price))
                    state.timestamps.append(datetime.now())
                    
                    # Update session stats
                    if state.session_high is None or price > state.session_high:
                        state.session_high = price
                    if state.session_low is None or price < state.session_low:
                        state.session_low = price
                    state.session_volume += volume
                    
                    # Calculate indicators
                    self.calculate_all_indicators(state)
                    
                    # Check alerts (adds to pending list)
                    alerts = self.check_all_alerts(state, price, volume)
                    new_alerts.extend(alerts)
                    
                    # Build dashboard field
                    field_value = self._build_dashboard_field(state, price, symbol_config)
                    
                    trend_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(state.trend, "➡️")
                    contract_name = CONTRACT_SPECS.get(symbol, {}).get('name', symbol)
                    
                    embed.add_field(
                        name=f"{trend_emoji} {symbol} ({contract_name})",
                        value=field_value,
                        inline=True
                    )
                    
                except Exception as e:
                    print(f"[monitor] Error processing {symbol}: {e}")
                    embed.add_field(
                        name=f"⚠️ {symbol}",
                        value=f"Error: {str(e)[:30]}",
                        inline=True
                    )
            
            # Add alerts to pending queue instead of sending immediately
            self.pending_alerts.extend(new_alerts)
            
            # Limit queue size
            if len(self.pending_alerts) > 20:
                self.pending_alerts = self.pending_alerts[-20:]
            
            # Update dashboard
            embed.set_footer(
                text=f"Next update: {UPDATE_INTERVAL}s | Pending alerts: {len(self.pending_alerts)} | "
                     f"Volatility: {self._get_avg_volatility()}"
            )
            
            if self.dashboard_msg is None:
                self.dashboard_msg = await channel.send(embed=embed)
            else:
                try:
                    await self.dashboard_msg.edit(embed=embed)
                except discord.NotFound:
                    self.dashboard_msg = await channel.send(embed=embed)
            
            print(f"[monitor] Updated | Pending alerts: {len(self.pending_alerts)}")
            
        except Exception as e:
            print(f"[monitor] Critical loop error: {e}")
            import traceback
            traceback.print_exc()

    @tasks.loop(seconds=30)  # Send batched alerts every 30 seconds
    async def alert_batch_loop(self):
        """Send aggregated alerts in batches to reduce spam"""
        if not self.pending_alerts or not self.alert_channel:
            return
        
        try:
            # Take up to MAX_ALERTS_PER_BATCH alerts
            batch = self.pending_alerts[:self.MAX_ALERTS_PER_BATCH]
            self.pending_alerts = self.pending_alerts[self.MAX_ALERTS_PER_BATCH:]
            
            if not batch:
                return
            
            # Group by severity
            critical_alerts = [a for a in batch if a.severity == 'critical']
            warning_alerts = [a for a in batch if a.severity == 'warning']
            info_alerts = [a for a in batch if a.severity == 'info']
            
            # Send critical alerts individually for visibility
            for alert in critical_alerts:
                await self.send_alert(self.alert_channel, alert)
                await asyncio.sleep(0.5)  # Small delay between critical alerts
            
            # Batch warning and info alerts together
            if warning_alerts or info_alerts:
                await self.send_batch_alert(self.alert_channel, warning_alerts + info_alerts)
            
            print(f"[alerts] Sent batch: {len(critical_alerts)} critical, {len(warning_alerts)} warning, {len(info_alerts)} info")
            
        except Exception as e:
            print(f"[alerts] Error sending batch: {e}")

    async def send_alert(self, channel: discord.TextChannel, alert: Alert):
        """Send individual alert embed"""
        color_map = {
            'info': COLORS['info'],
            'warning': COLORS['warning'],
            'critical': COLORS['danger']
        }
        
        embed = discord.Embed(
            title=f"🚨 {alert.symbol} - {alert.alert_type.replace('_', ' ').title()}",
            description=alert.message,
            color=color_map.get(alert.severity, EMBED_COLOR),
            timestamp=alert.timestamp
        )
        
        embed.add_field(
            name="💰 Price",
            value=f"{alert.price:,.2f}",
            inline=True
        )
        
        for key, value in alert.context.items():
            if value is None:
                continue
            if isinstance(value, float):
                value = f"{value:.2f}"
            elif isinstance(value, int):
                value = f"{value:,}"
            
            embed.add_field(
                name=key.replace('_', ' ').title(),
                value=str(value),
                inline=True
            )
        
        if alert.suggested_action:
            embed.add_field(
                name="💡 Action",
                value=alert.suggested_action,
                inline=False
            )
        
        await channel.send(embed=embed)

    async def send_batch_alert(self, channel: discord.TextChannel, alerts: List[Alert]):
        """Send multiple alerts in one embed"""
        if not alerts:
            return
        
        embed = discord.Embed(
            title=f"📢 Trading Alerts ({len(alerts)})",
            description=f"Aggregated alerts from {datetime.now().strftime('%H:%M:%S')}",
            color=COLORS['warning']
        )
        
        for alert in alerts[:10]:  # Max 10 per batch embed
            value = f"**Price:** {alert.price:,.2f}"
            if alert.suggested_action:
                value += f"\n💡 {alert.suggested_action}"
            
            emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(alert.severity, "⚪")
            
            embed.add_field(
                name=f"{emoji} {alert.symbol}: {alert.alert_type.replace('_', ' ').title()}",
                value=value,
                inline=False
            )
        
        if len(alerts) > 10:
            embed.set_footer(text=f"+ {len(alerts) - 10} more alerts pending...")
        
        await channel.send(embed=embed)

    def _build_dashboard_field(self, state: SymbolState, price: float, symbol_config: Dict) -> str:
        """Build rich dashboard field for a symbol"""
        lines = []
        
        # Price and change
        if len(state.prices) > 1:
            prev = state.prices[-2]
            change = price - prev
            change_pct = (change / prev) * 100
            emoji = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
            lines.append(f"**Price:** {price:,.2f} {emoji} {change:+.2f} ({change_pct:+.2f}%)")
        else:
            lines.append(f"**Price:** {price:,.2f}")
        
        # VWAP
        if state.vwap:
            vwap_emoji = "🟢" if price > state.vwap else "🔴"
            lines.append(f"**VWAP:** {state.vwap:,.2f} {vwap_emoji}")
        
        # Key indicators
        indicators = []
        if state.rsi:
            rsi_emoji = "🔥" if state.rsi > 70 else "❄️" if state.rsi < 30 else "⚡"
            indicators.append(f"RSI{rsi_emoji}{state.rsi:.0f}")
        if state.atr:
            indicators.append(f"ATR{state.atr:.1f}")
        if indicators:
            lines.append(f"**{' | '.join(indicators)}**")
        
        # Trend & Volatility
        vol_emoji = {"low": "😴", "normal": "⚡", "high": "🔥", "extreme": "💥"}.get(state.volatility_regime, "⚡")
        lines.append(f"**Trend:** {state.trend.title()} | **Vol:** {vol_emoji} {state.volatility_regime}")
        
        # Near S/R levels (only strong ones)
        sr_near = []
        for r in state.resistance_levels[:2]:
            if r.strength >= 0.6:
                dist = abs(r.price - price) / price * 100
                if dist < 0.3:
                    sr_near.append(f"R{r.price:.0f}")
        for s in state.support_levels[:2]:
            if s.strength >= 0.6:
                dist = abs(s.price - price) / price * 100
                if dist < 0.3:
                    sr_near.append(f"S{s.price:.0f}")
        
        if sr_near:
            lines.append(f"**Levels:** {', '.join(sr_near)}")
        
        return "\n".join(lines)

    def _get_avg_volatility(self) -> str:
        """Get average volatility across all symbols"""
        regimes = [s.volatility_regime for s in self.states.values()]
        if not regimes:
            return "N/A"
        
        counts = {"low": 0, "normal": 0, "high": 0, "extreme": 0}
        for r in regimes:
            counts[r] = counts.get(r, 0) + 1
        
        dominant = max(counts, key=counts.get)
        emoji = {"low": "😴", "normal": "⚡", "high": "🔥", "extreme": "💥"}
        return f"{emoji.get(dominant, '⚡')} {dominant}"

    # ==================== DISCORD COMMANDS ====================
    
    @commands.command(name="position")
    async def position_size(self, ctx, symbol: str, account_size: float, 
                           risk_percent: float, entry: float, stop: float):
        """Calculate position size for a trade"""
        symbol = symbol.upper()
        if symbol not in CONTRACT_SPECS:
            await ctx.send(f"❌ Symbol {symbol} not configured. Available: {', '.join(CONTRACT_SPECS.keys())}")
            return
        
        config = CONTRACT_SPECS[symbol]
        point_value = config.get('point_value', 50)
        tick_size = config.get('tick_size', 0.25)
        tick_value = config.get('tick_value', 12.50)
        
        risk_amount = account_size * (risk_percent / 100)
        stop_points = abs(entry - stop)
        stop_ticks = stop_points / tick_size
        risk_per_contract = stop_ticks * tick_value
        
        if risk_per_contract == 0:
            await ctx.send("❌ Stop loss too close to entry")
            return
        
        max_contracts = int(risk_amount / risk_per_contract)
        
        if max_contracts < 1:
            await ctx.send(f"❌ Risk too small. Need ${risk_per_contract:.2f}/contract, you allocated ${risk_amount:.2f}")
            return
        
        embed = discord.Embed(
            title=f"📊 Position Size: {symbol}",
            color=COLORS['success']
        )
        
        embed.add_field(name="Account", value=f"${account_size:,.0f}", inline=True)
        embed.add_field(name="Risk", value=f"{risk_percent}% (${risk_amount:.0f})", inline=True)
        embed.add_field(name="Entry→Stop", value=f"{entry:.2f}→{stop:.2f}", inline=True)
        embed.add_field(name="Stop", value=f"{stop_points:.2f}pts ({stop_ticks:.0f}ticks)", inline=True)
        embed.add_field(name="Risk/Contract", value=f"${risk_per_contract:.2f}", inline=True)
        embed.add_field(name="✅ CONTRACTS", value=f"**{max_contracts}**", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name="levels")
    async def show_levels(self, ctx, symbol: str):
        """Show support/resistance levels for a symbol"""
        symbol = symbol.upper()
        if symbol not in self.states:
            await ctx.send(f"❌ Symbol {symbol} not found")
            return
        
        state = self.states[symbol]
        
        embed = discord.Embed(
            title=f"🎯 Key Levels: {symbol}",
            color=EMBED_COLOR
        )
        
        if state.support_levels:
            supports = "\n".join([
                f"{i+1}. {level.price:.2f} (S:{level.strength:.0%}, T:{level.touches})"
                for i, level in enumerate(state.support_levels[:5])
            ])
            embed.add_field(name="🟢 Support", value=supports or "None", inline=False)
        
        if state.resistance_levels:
            resistances = "\n".join([
                f"{i+1}. {level.price:.2f} (S:{level.strength:.0%}, T:{level.touches})"
                for i, level in enumerate(state.resistance_levels[:5])
            ])
            embed.add_field(name="🔴 Resistance", value=resistances or "None", inline=False)
        
        if state.vwap:
            embed.add_field(name="⚖️ VWAP", value=f"{state.vwap:.2f}", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command(name="status")
    async def cmd_status(self, ctx):
        """Show bot status"""
        embed = discord.Embed(
            title="🤖 Bot Status",
            color=COLORS['success']
        )
        
        total_candles = sum(len(s.prices) for s in self.states.values())
        
        embed.add_field(
            name="Connection",
            value=f"✅ Online\nSession: {'Active' if self.session and not self.session.closed else 'Closed'}",
            inline=True
        )
        embed.add_field(
            name="Data",
            value=f"Symbols: {len(self.states)}\nCandles: {total_candles:,}",
            inline=True
        )
        embed.add_field(
            name="Loop",
            value=f"Running: {self.monitor_loop.is_running()}\nInterval: {UPDATE_INTERVAL}s",
            inline=True
        )
        embed.add_field(
            name="Alerts",
            value=f"Pending: {len(self.pending_alerts)}\nBatch: 30s",
            inline=True
        )
        
        await ctx.send(embed=embed)

    @monitor_loop.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    @alert_batch_loop.before_loop
    async def before_alert_batch(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(MonitorCog(bot))
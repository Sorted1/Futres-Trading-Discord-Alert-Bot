import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

# MAIN
PREFIX = config.get("main", {}).get("prefix", ".")
EMBED_COLOR = config.get("main", {}).get("embed_color", 0xFF0000)
GUILD_ID = int(config.get("main", {}).get("guild_id", 0))
OWNER_ID = int(config.get("main", {}).get("owner_id", 0))

# ACTIVITY
ACTIVITY = config.get("activity", {})

# LOG CHANNELS
MONITOR_ALERT_CHANNEL = int(config.get("logs", {}).get("monitor_alerts_channel", 0))

# MONITOR SETTINGS
MONITOR_CONFIG = config.get("monitor", {})
SYMBOLS = MONITOR_CONFIG.get("symbols", {})
UPDATE_INTERVAL = MONITOR_CONFIG.get("update_interval", 60)

# ============================================================
# ENHANCED TRADING CONFIGURATION (Add these to config.py)
# ============================================================

CONTRACT_SPECS = {
    "NQ=F": {"name": "E-mini NASDAQ-100", "tick_size": 0.25, "tick_value": 5.0, "point_value": 20.0},
    "ES=F": {"name": "E-mini S&P 500", "tick_size": 0.25, "tick_value": 12.50, "point_value": 50.0},
    "GC=F": {"name": "Gold", "tick_size": 0.10, "tick_value": 10.0, "point_value": 100.0},
}

DEFAULT_ALERT_THRESHOLDS = {
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "rsi_extreme_overbought": 80,
    "rsi_extreme_oversold": 20,
    "price_change_pct": 1.0,
    "price_change_points": 20,
    "volume_spike_multiplier": 3.0,
    "sr_proximity_pct": 0.1,
}

ALERT_COOLDOWNS = {
    "vwap_cross": 600,
    "rsi_divergence": 900,
    "rsi_overbought": 600,
    "rsi_oversold": 600,
    "support_test": 600,
    "resistance_test": 600,
    "bb_squeeze": 1800,
    "bb_breakout": 300,
    "volume_spike": 300,
    "extreme_volatility": 600,
    "trend_change": 1200,
    "price_spike": 300,
}

COLORS = {
    "info": 0x3498db,
    "success": 0x2ecc71,
    "warning": 0xf39c12,
    "danger": 0xe74c3c,
    "neutral": 0x95a5a6,
}
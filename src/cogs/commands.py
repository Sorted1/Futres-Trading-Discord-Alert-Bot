from discord.ext import commands
from utils.config import SYMBOLS  # Or your config dict
from discord import Embed

class CommandsCog(commands.Cog):
    """Commands for managing monitored symbols."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="add_symbol", aliases=["addsymbol"])
    async def add_symbol(self, ctx, symbol: str, alert_pct: float = 0.5, window_size: int = 5):
        """
        Add a symbol to the monitor.
        Example: .add_symbol NQ=F 0.5 5
        """
        symbol = symbol.upper()
        if symbol in SYMBOLS:
            await ctx.send(f"{symbol} is already being monitored.")
            return

        SYMBOLS[symbol] = {"alert_pct": alert_pct, "window_size": window_size}
        await ctx.send(f"✅ {symbol} added with {alert_pct}% alert threshold and {window_size} window size.")

    @commands.command(name="remove_symbol", aliases=["removesymbol"])
    async def remove_symbol(self, ctx, symbol: str):
        symbol = symbol.upper()
        if symbol not in SYMBOLS:
            await ctx.send(f"{symbol} is not being monitored.")
            return

        del SYMBOLS[symbol]
        await ctx.send(f"❌ {symbol} removed from monitoring.")

    @commands.command(name="list_symbols", aliases=["listsymbols"])
    async def list_symbols(self, ctx):
        if not SYMBOLS:
            await ctx.send("No symbols are currently being monitored.")
            return
        embed = Embed(title="Monitored Symbols")
        for s, conf in SYMBOLS.items():
            embed.add_field(
                name=s,
                value=f"Alert: {conf.get('alert_pct', 0.5)}% | Window: {conf.get('window_size', 5)}",
                inline=False
            )
        await ctx.send(embed=embed)

# --- v2.x async setup ---
async def setup(bot):
    await bot.add_cog(CommandsCog(bot))
import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Awaitable

from .okx_client import TickerData
from .storage import AlertRule, AlertType, RuleStorage, format_price

logger = logging.getLogger(__name__)

AlertCallback = Callable[[AlertRule, TickerData, str], Awaitable[None]]

TICKER_THROTTLE_SECONDS = 1.0
PRICE_SAVE_INTERVAL_SECONDS = 5.0
HISTORY_CLEANUP_INTERVAL_SECONDS = 60.0


class PriceMonitor:
    def __init__(
        self,
        storage: RuleStorage,
        alert_callback: AlertCallback | None = None,
        cooldown_minutes: int = 60,
    ):
        self.storage = storage
        self.alert_callback = alert_callback
        self.cooldown_minutes = cooldown_minutes
        self._price_history: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        self._last_alert: dict[str, datetime] = {}
        self._last_ticker: dict[str, datetime] = {}
        self._pending_prices: dict[str, tuple[float, datetime]] = {}
        self._rules_cache: dict[str, list[AlertRule]] = {}
        self._rules_cache_time: datetime = datetime.min
        self._rules_cache_ttl = timedelta(seconds=10)
        self._last_history_cleanup: datetime = datetime.min
        self._save_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._save_task = asyncio.create_task(self._price_save_loop())

    async def stop(self) -> None:
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        await self._flush_prices()

    async def on_ticker(self, ticker: TickerData) -> None:
        now = datetime.now()
        inst_id = ticker.inst_id

        self._pending_prices[inst_id] = (ticker.last, ticker.ts)

        last = self._last_ticker.get(inst_id)
        if last and (now - last).total_seconds() < TICKER_THROTTLE_SECONDS:
            return
        self._last_ticker[inst_id] = now

        self._price_history[inst_id].append((ticker.ts, ticker.last))

        if (now - self._last_history_cleanup).total_seconds() >= HISTORY_CLEANUP_INTERVAL_SECONDS:
            self._cleanup_history(now)
            self._last_history_cleanup = now

        rules = await self._get_rules_cached(inst_id)
        for rule in rules:
            if not rule.enabled:
                continue
            await self._check_rule(rule, ticker)

    def _cleanup_history(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        for inst_id in list(self._price_history.keys()):
            history = self._price_history[inst_id]
            if not history:
                continue
            first_valid = 0
            for i, (ts, _) in enumerate(history):
                if ts > cutoff:
                    first_valid = i
                    break
            else:
                first_valid = len(history)
            if first_valid > 0:
                self._price_history[inst_id] = history[first_valid:]

    async def _get_rules_cached(self, inst_id: str) -> list[AlertRule]:
        now = datetime.now()
        if now - self._rules_cache_time > self._rules_cache_ttl:
            all_rules = await self.storage.get_all_rules()
            self._rules_cache = defaultdict(list)
            for r in all_rules:
                self._rules_cache[r.inst_id].append(r)
            self._rules_cache_time = now
        return self._rules_cache.get(inst_id, [])

    def invalidate_rules_cache(self) -> None:
        self._rules_cache.clear()
        self._rules_cache_time = datetime.min

    async def _price_save_loop(self) -> None:
        while True:
            await asyncio.sleep(PRICE_SAVE_INTERVAL_SECONDS)
            await self._flush_prices()

    async def _flush_prices(self) -> None:
        if not self._pending_prices:
            return
        pending = self._pending_prices.copy()
        self._pending_prices.clear()
        for inst_id, (price, ts) in pending.items():
            await self.storage.save_price(inst_id, price, ts)

    async def _check_rule(self, rule: AlertRule, ticker: TickerData) -> None:
        if rule.alert_type in (AlertType.PRICE_ABOVE, AlertType.PRICE_BELOW):
            await self._check_price_rule(rule, ticker)
        elif rule.alert_type in (AlertType.CHANGE_UP, AlertType.CHANGE_DOWN):
            await self._check_change_rule(rule, ticker)

    async def _check_price_rule(self, rule: AlertRule, ticker: TickerData) -> None:
        triggered = False
        if rule.alert_type == AlertType.PRICE_ABOVE and ticker.last >= rule.threshold:
            triggered = True
        elif rule.alert_type == AlertType.PRICE_BELOW and ticker.last <= rule.threshold:
            triggered = True

        if triggered:
            if await self._should_alert(rule, ticker):
                message = self._format_price_alert(rule, ticker)
                await self._trigger_alert(rule, ticker, message)

    async def _check_change_rule(self, rule: AlertRule, ticker: TickerData) -> None:
        history = self._price_history.get(ticker.inst_id, [])
        cutoff = datetime.now() - timedelta(minutes=rule.interval_minutes)
        relevant = [(ts, p) for ts, p in history if ts >= cutoff]

        if len(relevant) < 2:
            return

        start_price = relevant[0][1]
        end_price = ticker.last
        change_pct = ((end_price - start_price) / start_price) * 100

        triggered = False
        if rule.alert_type == AlertType.CHANGE_UP and change_pct >= rule.threshold:
            triggered = True
        elif rule.alert_type == AlertType.CHANGE_DOWN and change_pct <= -rule.threshold:
            triggered = True

        if triggered:
            if await self._should_alert(rule, ticker):
                message = self._format_change_alert(rule, ticker, change_pct, start_price)
                await self._trigger_alert(rule, ticker, message)

    async def _should_alert(self, rule: AlertRule, ticker: TickerData) -> bool:
        key = f"{rule.id}:{ticker.inst_id}"
        last = self._last_alert.get(key)
        if last and datetime.now() - last < timedelta(minutes=self.cooldown_minutes):
            return False
        return True

    async def _trigger_alert(
        self, rule: AlertRule, ticker: TickerData, message: str
    ) -> None:
        key = f"{rule.id}:{ticker.inst_id}"
        self._last_alert[key] = datetime.now()

        rule.triggered_at = datetime.now().isoformat()
        await self.storage.update_rule(rule)

        logger.info(f"触发告警: {message}")

        if self.alert_callback:
            await self.alert_callback(rule, ticker, message)

    def _format_price_alert(self, rule: AlertRule, ticker: TickerData) -> str:
        emoji = "📈" if rule.alert_type == AlertType.PRICE_ABOVE else "📉"
        direction = "突破" if rule.alert_type == AlertType.PRICE_ABOVE else "跌破"
        return (
            f"{emoji} 【价格告警】\n"
            f"品种: {ticker.inst_id}\n"
            f"当前价格: ${format_price(ticker.last)}\n"
            f"已{direction}目标价位: ${format_price(rule.threshold)}\n"
            f"时间: {ticker.ts:%Y-%m-%d %H:%M:%S}"
        )

    def _format_change_alert(
        self, rule: AlertRule, ticker: TickerData, change_pct: float, start_price: float
    ) -> str:
        emoji = "⬆️" if rule.alert_type == AlertType.CHANGE_UP else "⬇️"
        direction = "涨幅" if rule.alert_type == AlertType.CHANGE_UP else "跌幅"
        return (
            f"{emoji} 【波动告警】\n"
            f"品种: {ticker.inst_id}\n"
            f"当前价格: ${format_price(ticker.last)}\n"
            f"{rule.interval_minutes}分钟{direction}: {abs(change_pct):.2f}%\n"
            f"起始价格: ${format_price(start_price)}\n"
            f"时间: {ticker.ts:%Y-%m-%d %H:%M:%S}"
        )

    @staticmethod
    def create_rule_id() -> str:
        return str(uuid.uuid4())[:8]

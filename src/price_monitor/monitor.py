import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Awaitable

from .okx_client import TickerData
from .storage import AlertRule, AlertType, RuleStorage

logger = logging.getLogger(__name__)

AlertCallback = Callable[[AlertRule, TickerData, str], Awaitable[None]]


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

    async def on_ticker(self, ticker: TickerData) -> None:
        self._price_history[ticker.inst_id].append((ticker.ts, ticker.last))
        cutoff = datetime.now() - timedelta(hours=1)
        self._price_history[ticker.inst_id] = [
            (ts, p) for ts, p in self._price_history[ticker.inst_id] if ts > cutoff
        ]

        await self.storage.save_price(ticker.inst_id, ticker.last, ticker.ts)

        rules = await self.storage.get_rules_by_inst(ticker.inst_id)
        for rule in rules:
            if not rule.enabled:
                continue
            await self._check_rule(rule, ticker)

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
            f"当前价格: ${ticker.last:,.2f}\n"
            f"已{direction}目标价位: ${rule.threshold:,.2f}\n"
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
            f"当前价格: ${ticker.last:,.2f}\n"
            f"{rule.interval_minutes}分钟{direction}: {abs(change_pct):.2f}%\n"
            f"起始价格: ${start_price:,.2f}\n"
            f"时间: {ticker.ts:%Y-%m-%d %H:%M:%S}"
        )

    @staticmethod
    def create_rule_id() -> str:
        return str(uuid.uuid4())[:8]

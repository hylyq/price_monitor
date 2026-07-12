import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any

try:
    import redis.asyncio as redis
except ImportError:
    raise ImportError("请安装 redis: uv add redis")

logger = logging.getLogger(__name__)

REDIS_KEY_RULES = "price_monitor:rules"
REDIS_KEY_PRICES = "price_monitor:prices"


def format_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        formatted = f"{price:.4f}".rstrip("0").rstrip(".")
        return formatted
    elif price >= 0.01:
        formatted = f"{price:.4f}".rstrip("0").rstrip(".")
        return formatted
    else:
        str_price = f"{price:.12f}".rstrip("0")
        if str_price.endswith("."):
            str_price = str_price[:-1]
        return str_price


class AlertType(Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    CHANGE_UP = "change_up"
    CHANGE_DOWN = "change_down"


@dataclass
class AlertRule:
    id: str
    inst_id: str
    alert_type: AlertType
    threshold: float
    interval_minutes: int = 0
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    triggered_at: str | None = None
    base_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["alert_type"] = self.alert_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlertRule":
        data = data.copy()
        data["alert_type"] = AlertType(data["alert_type"])
        return cls(**data)

    def get_description(self) -> str:
        if self.alert_type == AlertType.PRICE_ABOVE:
            return f"📈 {self.inst_id} 价格 >= ${format_price(self.threshold)}"
        elif self.alert_type == AlertType.PRICE_BELOW:
            return f"📉 {self.inst_id} 价格 <= ${format_price(self.threshold)}"
        elif self.alert_type == AlertType.CHANGE_UP:
            return f"⬆️ {self.inst_id} {self.interval_minutes}分钟涨幅 >= {self.threshold}%"
        elif self.alert_type == AlertType.CHANGE_DOWN:
            return f"⬇️ {self.inst_id} {self.interval_minutes}分钟跌幅 >= {self.threshold}%"
        return f"未知规则: {self.id}"


class RuleStorage:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
    ):
        if redis_url != "redis://localhost:6379":
            self.redis = redis.from_url(redis_url)
        else:
            self.redis = redis.Redis(host=redis_host, port=redis_port, db=redis_db)
        self._rules_cache: list[AlertRule] | None = None
        self._rules_cache_dirty = True

    def _invalidate_rules_cache(self) -> None:
        self._rules_cache = None
        self._rules_cache_dirty = True

    async def _get_rules_cache(self) -> list[AlertRule]:
        if self._rules_cache is None or self._rules_cache_dirty:
            items = await self.redis.hgetall(REDIS_KEY_RULES)
            self._rules_cache = [
                AlertRule.from_dict(json.loads(data)) for data in items.values()
            ]
            self._rules_cache_dirty = False
        return self._rules_cache

    async def add_rule(self, rule: AlertRule) -> None:
        await self.redis.hset(REDIS_KEY_RULES, rule.id, json.dumps(rule.to_dict()))
        self._invalidate_rules_cache()
        logger.info(f"添加规则: {rule.id} - {rule.get_description()}")

    async def remove_rule(self, rule_id: str) -> bool:
        result = await self.redis.hdel(REDIS_KEY_RULES, rule_id)
        if result:
            self._invalidate_rules_cache()
            logger.info(f"删除规则: {rule_id}")
        return result > 0

    async def get_rule(self, rule_id: str) -> AlertRule | None:
        rules = await self._get_rules_cache()
        for r in rules:
            if r.id == rule_id:
                return r
        return None

    async def get_all_rules(self) -> list[AlertRule]:
        return list(await self._get_rules_cache())

    async def get_rules_by_inst(self, inst_id: str) -> list[AlertRule]:
        rules = await self._get_rules_cache()
        return [r for r in rules if r.inst_id == inst_id]

    async def update_rule(self, rule: AlertRule) -> None:
        await self.redis.hset(REDIS_KEY_RULES, rule.id, json.dumps(rule.to_dict()))
        self._invalidate_rules_cache()

    async def clear_all_rules(self) -> None:
        await self.redis.delete(REDIS_KEY_RULES)
        self._invalidate_rules_cache()
        logger.info("清除所有规则")

    async def save_price(self, inst_id: str, price: float, ts: datetime) -> None:
        key = f"{REDIS_KEY_PRICES}:{inst_id}"
        data = {"price": price, "ts": ts.isoformat()}
        await self.redis.rpush(key, json.dumps(data))
        await self.redis.ltrim(key, -1000, -1)

    async def save_prices_batch(self, updates: list[tuple[str, float, datetime]]) -> None:
        if not updates:
            return
        async with self.redis.pipeline() as pipe:
            for inst_id, price, ts in updates:
                key = f"{REDIS_KEY_PRICES}:{inst_id}"
                data = {"price": price, "ts": ts.isoformat()}
                pipe.rpush(key, json.dumps(data))
                pipe.ltrim(key, -1000, -1)
            await pipe.execute()

    async def get_price_history(
        self, inst_id: str, since_minutes: int
    ) -> list[tuple[datetime, float]]:
        key = f"{REDIS_KEY_PRICES}:{inst_id}"
        items = await self.redis.lrange(key, 0, -1)
        cutoff = datetime.now().timestamp() - since_minutes * 60

        result = []
        for item in items:
            data = json.loads(item)
            ts = datetime.fromisoformat(data["ts"])
            if ts.timestamp() >= cutoff:
                result.append((ts, data["price"]))
        return result

    async def close(self) -> None:
        await self.redis.aclose()

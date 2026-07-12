import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

DEFAULT_OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"


@dataclass
class TickerData:
    inst_id: str
    last: float
    high_24h: float
    low_24h: float
    vol_24h: float
    ts: datetime

    @classmethod
    def from_okx(cls, data: dict) -> "TickerData":
        return cls(
            inst_id=data["instId"],
            last=float(data["last"]),
            high_24h=float(data["high24h"]),
            low_24h=float(data["low24h"]),
            vol_24h=float(data["vol24h"]),
            ts=datetime.fromtimestamp(int(data["ts"]) / 1000),
        )


class OKXClient:
    def __init__(
        self,
        ws_url: str = DEFAULT_OKX_WS_URL,
        on_ticker: Callable[[TickerData], None] | None = None,
        ping_interval: float = 25.0,
        reconnect_delay: float = 5.0,
    ):
        self.ws_url = ws_url
        self.on_ticker = on_ticker
        self.ping_interval = ping_interval
        self.reconnect_delay = reconnect_delay
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._connected = False
        self._subscribed: set[str] = set()
        self._prices: dict[str, TickerData] = {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as e:
                logger.error(f"OKX WebSocket连接错误: {e}")
                if self._running:
                    logger.info(f"{self.reconnect_delay}秒后重连...")
                    await asyncio.sleep(self.reconnect_delay)

    async def _connect_and_run(self) -> None:
        logger.info(f"连接OKX WebSocket: {self.ws_url}")
        async with websockets.connect(self.ws_url) as ws:
            self._ws = ws
            self._connected = True
            logger.info("OKX WebSocket连接成功")

            if self._subscribed:
                await self._resubscribe()

            ping_task = asyncio.create_task(self._ping_loop())
            try:
                async for message in ws:
                    await self._handle_message(message)
            finally:
                self._connected = False
                # ── Clear stale prices so callers don't serve frozen data
                #    during reconnection windows.
                if self._prices:
                    stale_ids = list(self._prices.keys())
                    self._prices.clear()
                    logger.warning(
                        "WebSocket 断开，已清除 %d 个品种的过期价格缓存: %s",
                        len(stale_ids), stale_ids,
                    )
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

    async def _ping_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.ping_interval)
            if self._ws:
                try:
                    await self._ws.send("ping")
                except Exception:
                    logger.warning("Ping 发送失败，连接可能已断开，触发重连...")
                    # Force-close so the _connect_and_run receive loop exits
                    # and the outer connect() loop schedules a reconnect.
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    break

    async def _handle_message(self, message: str) -> None:
        if message == "pong":
            return

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"无法解析消息: {message[:100]}")
            return

        if "event" in data:
            event = data["event"]
            if event == "subscribe":
                logger.info(f"订阅成功: {data.get('arg', {})}")
            elif event == "error":
                logger.error(f"订阅错误: {data.get('msg', 'unknown')}")
            return

        if "arg" in data and "data" in data:
            channel = data["arg"].get("channel", "")
            if channel == "tickers":
                for item in data["data"]:
                    try:
                        ticker = TickerData.from_okx(item)
                    except (KeyError, ValueError, TypeError) as exc:
                        logger.warning(
                            "解析 ticker 数据失败: %s — item=%s",
                            exc, json.dumps(item),
                        )
                        continue
                    self._prices[ticker.inst_id] = ticker
                    if self.on_ticker:
                        self.on_ticker(ticker)

    async def subscribe(self, inst_ids: list[str]) -> None:
        for inst_id in inst_ids:
            self._subscribed.add(inst_id)

        if self._ws:
            # ── Send ALL subscribed instruments, not just the new ones.
            #    Some WS servers treat a per-channel subscribe as a replacement
            #    rather than an append.  Always sending the full set ensures
            #    instruments subscribed earlier (e.g. at startup from saved
            #    rules) aren't silently dropped when a new instrument is added
            #    on the same channel later (e.g. via auto-subscribe in tools).
            await self._send_subscribe_message(list(self._subscribed))
            logger.info(
                "订阅品种: %s（当前全部订阅: %s）",
                inst_ids, list(self._subscribed),
            )

    async def _send_subscribe_message(self, inst_ids: list[str]) -> None:
        """Send a single subscribe message for the given instruments."""
        if not self._ws:
            return
        args = [{"channel": "tickers", "instId": inst_id} for inst_id in inst_ids]
        msg = {"op": "subscribe", "args": args}
        await self._ws.send(json.dumps(msg))

    async def _resubscribe(self) -> None:
        if self._subscribed and self._ws:
            await self._send_subscribe_message(list(self._subscribed))
            logger.info(f"重新订阅品种: {list(self._subscribed)}")

    async def unsubscribe(self, inst_ids: list[str]) -> None:
        for inst_id in inst_ids:
            self._subscribed.discard(inst_id)

        if self._ws:
            args = [{"channel": "tickers", "instId": inst_id} for inst_id in inst_ids]
            msg = {"op": "unsubscribe", "args": args}
            await self._ws.send(json.dumps(msg))
            logger.info(f"取消订阅品种: {inst_ids}")

    def get_price(self, inst_id: str) -> float | None:
        ticker = self._prices.get(inst_id)
        return ticker.last if ticker else None

    def get_ticker(self, inst_id: str) -> TickerData | None:
        return self._prices.get(inst_id)

    def get_all_prices(self) -> dict[str, float]:
        return {inst_id: ticker.last for inst_id, ticker in self._prices.items()}

    async def close(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            logger.info("OKX WebSocket连接已关闭")

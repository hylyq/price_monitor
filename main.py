import asyncio
import logging
import os

from dotenv import load_dotenv

from price_monitor.okx_client import OKXClient
from price_monitor.storage import RuleStorage
from price_monitor.monitor import PriceMonitor
from price_monitor.commands import CommandHandler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_url = os.getenv("REDIS_URL", f"redis://{redis_host}:{redis_port}")

    okx_ws_url = os.getenv("OKX_WS_URL")

    storage = RuleStorage(redis_url=redis_url)
    okx_client = OKXClient(ws_url=okx_ws_url) if okx_ws_url else OKXClient()

    from larky import WeChatClient

    wechat_client = WeChatClient(source="price-monitor", redis_url=redis_url)

    async def alert_callback(rule, ticker, message):
        await wechat_client.notify(message, priority="high")

    monitor = PriceMonitor(storage=storage, alert_callback=alert_callback)
    okx_client.on_ticker = lambda ticker: asyncio.create_task(monitor.on_ticker(ticker))

    cmd_handler = CommandHandler(
        storage=storage,
        monitor=monitor,
        okx_client=okx_client,
        wechat_client=wechat_client,
    )

    rules = await storage.get_all_rules()
    inst_ids = list(set(r.inst_id for r in rules))
    if inst_ids:
        logger.info(f"恢复订阅品种: {inst_ids}")

    @wechat_client.message_handler
    async def handle_message(data: dict):
        text = data.get("text", "")
        await cmd_handler.handle_text(text, wechat_client)

    @wechat_client.status_handler
    async def handle_status(data: dict):
        status = data.get("status")
        if status == "online":
            logger.info("微信服务已上线")
            await wechat_client.notify("🤖 OKX价格监控服务已启动\n发送 /help 查看可用命令")

            if inst_ids:
                await okx_client.subscribe(inst_ids)

            asyncio.create_task(okx_client.connect())
        elif status == "offline":
            logger.warning("微信服务离线")
        elif data.get("need_login"):
            logger.error("微信服务需要重新登录")

    try:
        await wechat_client.run()
    finally:
        await okx_client.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())

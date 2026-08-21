from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from graphrag.messaging import rabbitmq_client as module
from graphrag.messaging.rabbitmq_client import RabbitMQClient


async def test_connect_provisions_all_durable_queues_before_publishers_can_run() -> None:
    client = RabbitMQClient()
    channel = AsyncMock()
    exchange = AsyncMock()
    queue = AsyncMock()
    channel.declare_exchange.return_value = exchange
    channel.declare_queue.return_value = queue
    pool = MagicMock()
    context = AsyncMock()
    context.__aenter__.return_value = channel
    pool.acquire.return_value = context
    client._channel_pool = pool

    await client.ensure_topology()

    assert channel.declare_exchange.await_count == 3
    assert channel.declare_queue.await_count == 6  # three work queues + three DLQs
    assert queue.bind.await_count == 3
    for call in channel.declare_queue.await_args_list:
        assert call.kwargs["durable"] is True


async def test_failed_singleton_connect_is_not_cached() -> None:
    await module.close_rabbitmq()
    broken = MagicMock()
    broken.connect = AsyncMock(side_effect=ConnectionError("broker down"))
    healthy = MagicMock()
    healthy.connect = AsyncMock()
    healthy.close = AsyncMock()

    with patch.object(module, "RabbitMQClient", side_effect=[broken, healthy]):
        with pytest.raises(ConnectionError, match="broker down"):
            await module.get_rabbitmq()
        assert await module.get_rabbitmq() is healthy

    await module.close_rabbitmq()
    healthy.close.assert_awaited_once()

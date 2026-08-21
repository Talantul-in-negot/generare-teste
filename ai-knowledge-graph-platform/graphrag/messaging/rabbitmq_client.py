"""aio-pika connection pool, publisher and consumer base."""

from __future__ import annotations

import asyncio
import json
from typing import Callable, Awaitable

import aio_pika
import structlog
from aio_pika import ExchangeType, Message, DeliveryMode
from aio_pika.pool import Pool

from graphrag.core.config import get_settings
from graphrag.core.exceptions import MessagingError
from graphrag.observability.correlation import current_correlation_id

log = structlog.get_logger(__name__)

MAX_RETRIES = 3


async def _make_connection():
    cfg = get_settings()
    return await aio_pika.connect_robust(cfg.rabbitmq_url)


async def _make_channel(connection_pool: Pool):
    async with connection_pool.acquire() as connection:
        return await connection.channel()


class RabbitMQClient:
    """
    Connection + channel pool with publisher and consumer helpers.
    Uses aio-pika's robust connection (auto-reconnects on failure).
    """

    def __init__(self):
        self._connection_pool: Pool | None = None
        self._channel_pool: Pool | None = None

    async def connect(self):
        self._connection_pool = Pool(_make_connection, max_size=5)
        self._channel_pool = Pool(
            lambda: _make_channel(self._connection_pool), max_size=20
        )
        # Durable exchanges do not retain messages when no queue is bound.
        # Provision every queue before the API can publish so worker startup
        # order or a temporary worker outage cannot silently drop accepted work.
        try:
            await self.ensure_topology()
        except Exception:
            await self.close()
            raise
        log.info("rabbitmq.connected")

    async def close(self):
        if self._channel_pool:
            await self._channel_pool.close()
        if self._connection_pool:
            await self._connection_pool.close()
        self._channel_pool = None
        self._connection_pool = None
        log.info("rabbitmq.closed")

    async def ensure_topology(self) -> None:
        """Declare all durable exchanges, work queues, bindings, and DLQs."""
        if not self._channel_pool:
            raise MessagingError("RabbitMQ not connected — call connect() first")
        from graphrag.messaging.exchanges import (
            EVAL_EXCHANGE,
            EVAL_QUEUE,
            EVAL_ROUTING_KEY,
            INGEST_EXCHANGE,
            INGEST_QUEUE,
            INGEST_ROUTING_KEY,
            QUERY_EXCHANGE,
            QUERY_QUEUE,
            QUERY_ROUTING_KEY,
        )

        topology = (
            (INGEST_EXCHANGE, INGEST_QUEUE, INGEST_ROUTING_KEY),
            (QUERY_EXCHANGE, QUERY_QUEUE, QUERY_ROUTING_KEY),
            (EVAL_EXCHANGE, EVAL_QUEUE, EVAL_ROUTING_KEY),
        )
        async with self._channel_pool.acquire() as channel:
            for exchange_name, queue_name, routing_key in topology:
                exchange = await channel.declare_exchange(
                    exchange_name, ExchangeType.TOPIC, durable=True
                )
                dlq_name = f"{queue_name}.dlq"
                await channel.declare_queue(dlq_name, durable=True)
                queue = await channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": "",
                        "x-dead-letter-routing-key": dlq_name,
                        "x-message-ttl": 86_400_000,
                    },
                )
                await queue.bind(exchange, routing_key=routing_key)
        log.info("rabbitmq.topology_ready", queues=len(topology))

    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        payload: dict,
        priority: int = 0,
    ):
        if not self._channel_pool:
            raise MessagingError("RabbitMQ not connected — call connect() first")

        async with self._channel_pool.acquire() as channel:
            exchange = await channel.declare_exchange(
                exchange_name,
                ExchangeType.TOPIC,
                durable=True,
            )
            body = json.dumps(payload).encode()
            correlation_id = str(payload.get("correlation_id") or current_correlation_id() or "")
            headers = {"x-correlation-id": correlation_id} if correlation_id else {}
            try:
                from opentelemetry.propagate import inject
                inject(headers)
            except ImportError:
                pass
            message = Message(
                body,
                delivery_mode=DeliveryMode.PERSISTENT,
                priority=priority,
                correlation_id=correlation_id or None,
                headers=headers or None,
            )
            await exchange.publish(message, routing_key=routing_key)
            log.info(
                "rabbitmq.published",
                exchange=exchange_name,
                routing_key=routing_key,
                bytes=len(body),
            )

    async def consume(
        self,
        exchange_name: str,
        queue_name: str,
        routing_key: str,
        handler: Callable[[dict], Awaitable[None]],
    ):
        """Start consuming messages from a queue. Runs until cancelled."""
        if not self._channel_pool:
            raise MessagingError("RabbitMQ not connected — call connect() first")

        async with self._channel_pool.acquire() as channel:
            await channel.set_qos(prefetch_count=1)

            exchange = await channel.declare_exchange(
                exchange_name, ExchangeType.TOPIC, durable=True
            )
            # Dead-letter queue
            dlq_name = f"{queue_name}.dlq"
            await channel.declare_queue(dlq_name, durable=True)

            queue = await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": dlq_name,
                    "x-message-ttl": 86400000,  # 24h
                },
            )
            await queue.bind(exchange, routing_key=routing_key)

            log.info(
                "rabbitmq.consuming",
                exchange=exchange_name,
                queue=queue_name,
            )

            async with queue.iterator() as q:
                async for message in q:
                    retries = int(
                        message.headers.get("x-retry-count", 0)
                        if message.headers else 0
                    )
                    try:
                        payload = json.loads(message.body)
                        if message.correlation_id and not payload.get("correlation_id"):
                            payload["correlation_id"] = message.correlation_id
                        otel_token = None
                        try:
                            from opentelemetry import context as otel_context
                            from opentelemetry.propagate import extract
                            otel_token = otel_context.attach(extract(dict(message.headers or {})))
                        except ImportError:
                            pass
                        try:
                            await handler(payload)
                        finally:
                            if otel_token is not None:
                                otel_context.detach(otel_token)
                        await message.ack()
                    except Exception as exc:  # broad: handler may raise anything; must not kill consumer loop
                        exc_type  = type(exc).__name__
                        exc_msg   = str(exc)[:300]
                        # Summarise original payload for DLQ — truncate large fields
                        try:
                            raw_payload = json.loads(message.body)
                            payload_summary = {
                                k: (str(v)[:80] if isinstance(v, str) else v)
                                for k, v in list(raw_payload.items())[:8]
                            }
                        except Exception:
                            payload_summary = {"raw": message.body[:200].decode(errors="replace")}

                        log.error(
                            "rabbitmq.handler_error",
                            exception_type=exc_type,
                            error=exc_msg,
                            retries=retries,
                            queue=queue_name,
                            message_id=str(message.message_id or ""),
                            payload_summary=payload_summary,
                        )

                        if retries < MAX_RETRIES:
                            backoff_s = min(2 ** retries, 30)  # 1s, 2s, 4s… cap 30s
                            log.info(
                                "rabbitmq.retry_backoff",
                                backoff_s=backoff_s,
                                attempt=retries + 1,
                            )
                            await asyncio.sleep(backoff_s)

                            new_headers = dict(message.headers or {})
                            new_headers["x-retry-count"]    = retries + 1
                            new_headers["x-last-error"]     = exc_msg
                            new_headers["x-exception-type"] = exc_type
                            retry_msg = Message(
                                message.body,
                                delivery_mode=message.delivery_mode,
                                priority=message.priority or 0,
                                headers=new_headers,
                                correlation_id=message.correlation_id,
                            )
                            await channel.default_exchange.publish(
                                retry_msg, routing_key=queue_name
                            )
                            await message.ack()
                        else:
                            # Build structured DLQ envelope so ops can triage without
                            # parsing raw RabbitMQ headers.
                            dlq_envelope = {
                                "dlq_reason":       "max_retries_exceeded",
                                "exception_type":   exc_type,
                                "error":            exc_msg,
                                "retry_count":      retries,
                                "queue":            queue_name,
                                "message_id":       str(message.message_id or ""),
                                "payload_summary":  payload_summary,
                            }
                            log.error("rabbitmq.dlq_sent", dlq=dlq_name, **dlq_envelope)
                            dlq_msg = Message(
                                json.dumps(dlq_envelope).encode(),
                                delivery_mode=DeliveryMode.PERSISTENT,
                                headers={
                                    "x-original-queue":  queue_name,
                                    "x-exception-type":  exc_type,
                                    "x-retry-count":     retries,
                                },
                                correlation_id=message.correlation_id,
                            )
                            await channel.default_exchange.publish(dlq_msg, routing_key=dlq_name)
                            await message.ack()  # ack original so it leaves the main queue


_client: RabbitMQClient | None = None
_client_lock: asyncio.Lock | None = None


async def get_rabbitmq() -> RabbitMQClient:
    """Return the singleton RabbitMQClient, safe against concurrent cold-start.

    Without a lock, two coroutines racing on startup both pass the ``None``
    check, each create a connection pool, and one pool leaks silently.
    The inner double-check after acquiring the lock prevents the race while
    keeping the fast path (already connected) lock-free.
    """
    global _client, _client_lock
    # Lazy lock creation — asyncio.Lock() must be created inside an event loop,
    # so we can't create it at module level.  Creating it here is safe because
    # asyncio does not yield between the check and the assignment (no `await`).
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    async with _client_lock:
        if _client is None:
            candidate = RabbitMQClient()
            await candidate.connect()
            _client = candidate
    return _client


async def close_rabbitmq() -> None:
    """Close and reset the process singleton when it was initialized."""
    global _client, _client_lock
    client, _client = _client, None
    if client is not None:
        await client.close()
    _client_lock = None

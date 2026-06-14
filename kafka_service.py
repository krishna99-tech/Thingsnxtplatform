"""
Apache Kafka integration: enriched telemetry topic + UI relay consumer.
Mobile/web clients do not talk to Kafka directly; they use /integrations/kafka/* HTTP/SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError

logger = logging.getLogger(__name__)

KAFKA_ENABLED = os.getenv("KAFKA_ENABLED", "true").lower() in ("1", "true", "yes")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC_TELEMETRY = os.getenv("KAFKA_TOPIC_TELEMETRY", "iot.telemetry.enriched")
KAFKA_CLIENT_ID = os.getenv("KAFKA_CLIENT_ID", "thingsnxt-api")

_producer: Optional[AIOKafkaProducer] = None
_produced_count = 0
_last_publish_error: Optional[str] = None
_relay_task: Optional[asyncio.Task] = None
_watchdog_task: Optional[asyncio.Task] = None


def kafka_bootstrap_masked() -> str:
    parts = [p.strip() for p in KAFKA_BOOTSTRAP_SERVERS.split(",") if p.strip()]
    return ",".join(parts[:2]) + ("…" if len(parts) > 2 else "")


def kafka_stats() -> Dict[str, Any]:
    is_ready = False
    # Check if producer exists and is specifically marked as connected via start()
    if _producer is not None and _last_publish_error is None:
        is_ready = True
        
    return {
        "enabled": KAFKA_ENABLED,
        "bootstrap": kafka_bootstrap_masked(),
        "topic_telemetry": KAFKA_TOPIC_TELEMETRY,
        "status": "connected" if is_ready else ("connecting" if KAFKA_ENABLED else "disabled"),
        "messages_produced": _produced_count,
        "last_publish_error": _last_publish_error,
    }


async def kafka_producer_watchdog():
    """Periodically verifies Kafka producer health and restarts if necessary."""
    while True:
        if KAFKA_ENABLED and (_producer is None or _last_publish_error):
            try:
                logger.info("📡 Kafka Watchdog: Producer unhealthy or disconnected, attempting start...")
                await start_kafka_producer()
            except Exception as e:
                logger.debug(f"Watchdog attempt failed: {e}")
        await asyncio.sleep(20) # Check every 20 seconds


async def start_kafka_producer() -> None:
    global _producer, _last_publish_error, _watchdog_task
    if not KAFKA_ENABLED:
        logger.info("Kafka disabled (KAFKA_ENABLED=false)")
        return
    if _producer is not None:
        return
    servers = [h.strip() for h in KAFKA_BOOTSTRAP_SERVERS.split(",") if h.strip()]
    try:
        _producer = AIOKafkaProducer(
            bootstrap_servers=servers,
            client_id=f"{KAFKA_CLIENT_ID}-producer",
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        )
        await _producer.start()
        logger.info("Kafka producer started (%s topic=%s)", servers, KAFKA_TOPIC_TELEMETRY)
        _last_publish_error = None
    except (KafkaConnectionError, OSError, Exception) as e:
        await stop_kafka_producer()
        _producer = None # Ensure it is reset so watchdog tries again
        _last_publish_error = str(e)
        logger.warning("Kafka producer could not start (watchdog will retry): %s", e)


async def stop_kafka_producer() -> None:
    global _producer, _watchdog_task
   
    if _producer is not None:
        try:
            await _producer.stop()
        except Exception as e:
            logger.debug("Kafka producer stop: %s", e)
        finally:
            _producer = None
            
    if _watchdog_task:
        _watchdog_task.cancel()
        _watchdog_task = None


async def publish_telemetry_enriched(
    *,
    user_id: str,
    device_id: str,
    patch: Dict[str, Any],
    derived: Dict[str, Any],
    ingest_source: str,
    timestamp_iso: str,
) -> None:
    global _produced_count, _last_publish_error, _producer
    if not KAFKA_ENABLED:
        return
    if _producer is None:
        await start_kafka_producer()
    if _producer is None:
        return

    record: Dict[str, Any] = {
        "type": "telemetry_enriched",
        "user_id": user_id,
        "device_id": device_id,
        "timestamp": timestamp_iso,
        "source": ingest_source,
        "patch": patch,
        "derived": derived,
    }
    try:
        key = user_id.encode("utf-8") if user_id else None
        await _producer.send_and_wait(KAFKA_TOPIC_TELEMETRY, value=record, key=key)
        _produced_count += 1
        _last_publish_error = None
    except Exception as e:
        _last_publish_error = str(e)
        logger.warning("Kafka publish failed: %s", e)


def schedule_publish_telemetry_enriched(**kwargs: Any) -> None:
    asyncio.create_task(_safe_publish(**kwargs))


async def _safe_publish(**kwargs: Any) -> None:
    try:
        await publish_telemetry_enriched(**kwargs)
    except Exception:
        logger.exception("Kafka background publish error")


async def kafka_ui_relay_worker() -> None:
    if not KAFKA_ENABLED:
        return
    from kafka_feed_manager import kafka_feed_manager

    servers = [h.strip() for h in KAFKA_BOOTSTRAP_SERVERS.split(",") if h.strip()]
    backoff = 5
    while True:
        consumer: Optional[AIOKafkaConsumer] = None
        try:
            consumer = AIOKafkaConsumer(
                KAFKA_TOPIC_TELEMETRY,
                bootstrap_servers=servers,
                group_id=f"{KAFKA_CLIENT_ID}-ui-relay",
                enable_auto_commit=True,
                auto_offset_reset="latest",
                value_deserializer=lambda b: json.loads(b.decode("utf-8")),
                client_id=f"{KAFKA_CLIENT_ID}-relay",
            )
            await consumer.start()
            logger.info("Kafka UI relay consumer started on %s", KAFKA_TOPIC_TELEMETRY)
            async for msg in consumer:
                try:
                    val = msg.value
                    if not isinstance(val, dict):
                        continue
                    uid = str(val.get("user_id") or "")
                    if uid:
                        await kafka_feed_manager.broadcast_user(uid, val)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Kafka relay handling error")
        except asyncio.CancelledError:
            logger.info("Kafka relay worker cancelled")
            raise
        except Exception as e:
            logger.warning("Kafka relay stopped (%s); retry in %ss", e, backoff)
            await asyncio.sleep(backoff)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:
                    pass


def start_kafka_relay_background() -> None:
    global _relay_task
    if not KAFKA_ENABLED:
        return
    if _relay_task and not _relay_task.done():
        return
    loop = asyncio.get_running_loop()
    _relay_task = loop.create_task(kafka_ui_relay_worker(), name="kafka_ui_relay")

def initialize_kafka_services() -> None:
    """Entry point to start all Kafka-related background tasks without blocking main thread."""
    global _watchdog_task
    if KAFKA_ENABLED:
        if _watchdog_task is None or _watchdog_task.done():
            _watchdog_task = asyncio.create_task(kafka_producer_watchdog(), name="kafka_watchdog")
        start_kafka_relay_background()


async def stop_kafka_relay() -> None:
    global _relay_task
    if _relay_task and not _relay_task.done():
        _relay_task.cancel()
        try:
            await _relay_task
        except asyncio.CancelledError:
            pass
    _relay_task = None

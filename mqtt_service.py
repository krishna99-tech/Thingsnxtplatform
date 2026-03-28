"""
MQTT bridge: device uplink telemetry and downlink commands (e.g. LED).
Topics (prefix via MQTT_TOPIC_PREFIX, default thingsnxt):

  Device → broker: {prefix}/device/{device_id}/telemetry
    JSON body matches HTTP TelemetryData:
    {"device_token": "<secret>", "data": {"temperature": 23, ...}}

  Broker → device: {prefix}/device/{device_id}/commands  (QoS 1)
    {"type": "led", "state": 1|0, "virtual_pin": "v0"? , "timestamp": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from aiomqtt import Client, MqttError
from fastapi import HTTPException
from pydantic import ValidationError

logger = logging.getLogger(__name__)

MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() in ("1", "true", "yes")
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = (os.getenv("MQTT_USER") or os.getenv("MQTT_USERNAME") or "").strip() or None
MQTT_PASSWORD = (os.getenv("MQTT_PASSWORD") or "").strip() or None
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "thingsnxt").strip("/ ")


def telemetry_subscribe_pattern() -> str:
    return f"{MQTT_TOPIC_PREFIX}/device/+/telemetry"


def telemetry_publish_topic(device_id: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/device/{device_id}/telemetry"


def commands_topic(device_id: str) -> str:
    return f"{MQTT_TOPIC_PREFIX}/device/{device_id}/commands"


def mqtt_config_summary() -> dict[str, Any]:
    return {
        "enabled": MQTT_ENABLED,
        "host": MQTT_HOST,
        "port": MQTT_PORT,
        "topic_prefix": MQTT_TOPIC_PREFIX,
        "telemetry_topic_example": telemetry_publish_topic("{device_id}"),
        "commands_topic_example": commands_topic("{device_id}"),
        "subscribe_pattern": telemetry_subscribe_pattern(),
    }


def _client_kwargs(identifier_suffix: str = "sub") -> dict[str, Any]:
    kw: dict[str, Any] = {
        "hostname": MQTT_HOST,
        "port": MQTT_PORT,
        "identifier": f"thingsnxt-{identifier_suffix}-{os.getpid()}",
    }
    if MQTT_USERNAME:
        kw["username"] = MQTT_USERNAME
    if MQTT_PASSWORD:
        kw["password"] = MQTT_PASSWORD
    return kw


async def publish_json(topic: str, payload: dict) -> None:
    if not MQTT_ENABLED:
        return
    body = json.dumps(payload, separators=(",", ":")).encode()
    try:
        async with Client(**_client_kwargs("pub")) as client:
            await client.publish(topic, body, qos=1)
    except MqttError as e:
        logger.warning("MQTT publish failed for %s: %s", topic, e)
    except OSError as e:
        logger.warning("MQTT publish I/O error for %s: %s", topic, e)


async def publish_led_command(device_id: str, state: bool, virtual_pin: Optional[str] = None) -> None:
    msg: dict[str, Any] = {
        "type": "led",
        "state": 1 if state else 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if virtual_pin:
        msg["virtual_pin"] = virtual_pin.lower()
    await publish_json(commands_topic(device_id), msg)


async def _handle_one_message(topic_str: str, payload: bytes) -> None:
    parts = topic_str.split("/")
    try:
        idx = parts.index("device")
        device_id = parts[idx + 1]
    except (ValueError, IndexError):
        logger.debug("Ignoring MQTT topic (unrecognized shape): %s", topic_str)
        return

    if not device_id:
        return

    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("MQTT telemetry invalid JSON for %s: %s", device_id, e)
        return

    from models import TelemetryData

    try:
        data = TelemetryData(**raw)
    except ValidationError as e:
        logger.warning("MQTT telemetry schema error for %s: %s", device_id, e)
        return

    try:
        from device_routes import ingest_device_telemetry

        await ingest_device_telemetry(device_id, data, ingest_source="mqtt")
    except HTTPException as e:
        logger.warning(
            "MQTT telemetry rejected for %s: %s — %s", device_id, e.status_code, e.detail
        )
    except Exception:
        logger.exception("MQTT telemetry handler error for %s", device_id)


async def mqtt_bridge_worker() -> None:
    """Subscribes to all device telemetry topics; reconnects on failure. Cancel task to stop."""
    if not MQTT_ENABLED:
        logger.info("MQTT bridge disabled (MQTT_ENABLED=false)")
        return

    backoff = 5
    while True:
        try:
            async with Client(**_client_kwargs("bridge")) as client:
                pattern = telemetry_subscribe_pattern()
                await client.subscribe(pattern, qos=1)
                logger.info("MQTT subscribed: %s @ %s:%s", pattern, MQTT_HOST, MQTT_PORT)
                async for message in client.messages:
                    await _handle_one_message(str(message.topic), message.payload)
        except asyncio.CancelledError:
            logger.info("MQTT bridge task cancelled")
            raise
        except MqttError as e:
            logger.warning("MQTT session ended (%s); reconnecting in %ss", e, backoff)
            await asyncio.sleep(backoff)
        except Exception:
            logger.exception("MQTT bridge error; reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)

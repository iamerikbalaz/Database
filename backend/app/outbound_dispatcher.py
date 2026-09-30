"""Bounded application-owned dispatcher. Disabled settings perform no external IO."""
import asyncio
import logging

import anyio
from sqlalchemy import text

from app.notion_outbound import process_next_sync, recover_interrupted_sync
from app.order_folders import process_next_folder_operation, recover_interrupted_folders

LOGGER = logging.getLogger(__name__)
DISPATCHER_LOCK = 793014628


async def run_outbound_dispatcher(database, settings, stop):
    if not settings.notion_outbound_enabled and not settings.order_folders_enabled: return
    connection = None
    try:
        if database.engine.dialect.name == "postgresql":
            connection = database.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": DISPATCHER_LOCK}): return
        if settings.notion_outbound_enabled: recover_interrupted_sync(database)
        if settings.order_folders_enabled: recover_interrupted_folders(database)
        while not stop.is_set():
            try:
                await process_next_sync(database, settings)
                await anyio.to_thread.run_sync(process_next_folder_operation, database, settings)
            except Exception:
                # Never print request properties, credentials or raw DB exceptions.
                LOGGER.warning("Outbound dispatcher operation failed; durable state retained")
            try: await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError: pass
    finally:
        if connection is not None:
            try: connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": DISPATCHER_LOCK})
            finally: connection.close()

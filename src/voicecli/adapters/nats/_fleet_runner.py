"""Wire roxabi-obs fleet reporter alongside NATS satellite adapters."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from roxabi_nats.adapter_base import NatsAdapterBase


async def run_adapter_with_fleet_reporter(
    adapter: NatsAdapterBase,
    nats_url: str,
) -> None:
    from roxabi_nats import nats_connect
    from roxabi_obs import cancel_fleet_reporter, start_fleet_reporter

    nc = await nats_connect(
        nats_url,
        identity_name=adapter._identity_name,
        inbox_prefix=adapter._inbox_prefix,
    )
    fleet_task = await start_fleet_reporter(nc)
    try:
        await adapter.run_embedded(nc)
    finally:
        await cancel_fleet_reporter(fleet_task)


def run_adapter_with_fleet_reporter_sync(
    adapter: NatsAdapterBase,
    nats_url: str,
) -> None:
    asyncio.run(run_adapter_with_fleet_reporter(adapter, nats_url))

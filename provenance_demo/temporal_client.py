from temporalio.client import Client

from . import config


async def connect() -> Client:
    return await Client.connect(config.temporal_address(), namespace=config.temporal_namespace())

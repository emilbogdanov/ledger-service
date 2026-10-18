import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from ledger.api.app import openapi_schema
from ledger.application.clients import RegisteredClient
from ledger.config import Settings
from ledger.domain.errors import InvalidValueError
from ledger.main import build_container


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger")
    commands = parser.add_subparsers(dest="command", required=True)
    create_client = commands.add_parser(
        "create-client",
        help="register an API client and print its key",
        description="The key is printed once and only its hash is stored; keep it safe.",
    )
    create_client.add_argument("--name", required=True, help="who the client is, e.g. a service")
    commands.add_parser(
        "openapi", help="print the API schema as JSON, e.g. to generate a typed client"
    )
    args = parser.parse_args(argv)

    match args.command:
        case "create-client":
            return _create_client(args.name)
        case _:
            return _print_openapi()


def _create_client(name: str) -> int:
    try:
        registered = asyncio.run(_register(name))
    except InvalidValueError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1
    sys.stdout.write(
        f"created client {registered.client.name} ({registered.client.id})\n"
        f"api key: {registered.api_key}\n"
    )
    return 0


async def _register(name: str) -> RegisteredClient:
    container = build_container(Settings())
    try:
        return await container.clients().register(name)
    finally:
        await container.close()


def _print_openapi() -> int:
    # ASCII-only output survives a shell redirect on any platform's console encoding.
    sys.stdout.write(json.dumps(openapi_schema(), indent=2, ensure_ascii=True) + "\n")
    return 0

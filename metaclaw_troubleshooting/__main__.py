"""Run the repository-local troubleshooting MVP."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from ipaddress import ip_address
from pathlib import Path


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _serve(host: str, port: int, database_path: Path) -> None:
    import uvicorn

    from .api import create_app

    uvicorn.run(
        create_app(database_path=database_path),
        host=host,
        port=port,
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18080, type=int)
    parser.add_argument(
        "--database",
        default=Path.home() / ".metaclaw" / "troubleshooting.db",
        type=Path,
    )
    args = parser.parse_args(argv)
    if not _is_loopback_host(args.host):
        parser.error("non-loopback binding is disabled until troubleshooting RBAC/SSO is implemented")
    _serve(args.host, args.port, args.database.expanduser())


if __name__ == "__main__":
    main()

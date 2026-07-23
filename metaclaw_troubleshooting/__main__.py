"""Run the repository-local troubleshooting MVP."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18080, type=int)
    args = parser.parse_args()
    uvicorn.run(
        "metaclaw_troubleshooting.api:app",
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()

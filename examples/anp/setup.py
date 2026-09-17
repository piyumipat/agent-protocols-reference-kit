"""Prepare shared local TLS trust and DID credentials for separate ANP processes."""

from __future__ import annotations

from examples.anp.demo_common import DemoConfig, setup_demo


def main() -> None:
    config = DemoConfig()
    setup_demo(config)
    print(f"Prepared local ANP credentials and TLS trust in {config.directory}")
    print("Start facts, review, and coordinator agents, then run examples.anp.user")


if __name__ == "__main__":
    main()

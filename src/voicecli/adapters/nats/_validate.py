"""Re-export NATS token validation from ``roxabi_satellite``."""

from roxabi_satellite.tokens import validate_nats_single_token, validate_nats_token

__all__ = ["validate_nats_single_token", "validate_nats_token"]

"""Claude API cost of one response, from its usage block."""

from __future__ import annotations

# USD per million tokens: (input, output). Cache writes (5-minute) cost 1.25x input, cache reads 0.1x.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def cost_usd(model: str, usage) -> float:
    price_in, price_out = PRICES[model]
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        usage.input_tokens * price_in
        + cache_write * price_in * 1.25
        + cache_read * price_in * 0.10
        + usage.output_tokens * price_out
    ) / 1_000_000

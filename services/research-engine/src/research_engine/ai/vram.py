"""VRAM estimates from a model's own metadata, and the documented reduction when it does not fit.

    total = weights (the model file) + KV cache + overhead
    KV cache = 2 * layers * context * KV heads * head size * 2 bytes (f16 keys and values)
    overhead = 512 MiB (CUDA context, compute buffers) + 5 % of the weights

It is an estimate: hybrid-attention models need less KV cache than this says (a safe error), and the
runtime's own report of a loaded model (`size_vram`) is what is shown once a model runs. When the
estimate does not fit the free VRAM, the context is halved down to MIN_CTX; still too big, the model
runs partly on the CPU if the profile allows it, and is refused otherwise. Nothing loops.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from research_engine.domain import AiDevice, AiVramEstimate

MIB: Final = 1024 * 1024
MIN_CTX: Final = 2048
OVERHEAD_MIB: Final = 512
OVERHEAD_SHARE: Final = 0.05
#: A model that would leave less than this much VRAM free is treated as not fitting.
HEADROOM_MIB: Final = 256


@dataclass(frozen=True, slots=True)
class ModelShape:
    """What the runtime reports about a model's architecture (Ollama `/api/show` model_info)."""

    weights_bytes: int
    layers: int | None
    kv_heads: int | None
    head_dim: int | None
    context_length: int | None


def shape_from_info(weights_bytes: int, info: Mapping[str, object]) -> ModelShape:
    """Reads `<architecture>.block_count` and friends; missing keys stay unknown."""
    architecture = info.get("general.architecture")
    prefix = f"{architecture}." if isinstance(architecture, str) else ""

    def number(key: str) -> int | None:
        value = info.get(prefix + key)
        return value if isinstance(value, int) and value > 0 else None

    heads = number("attention.head_count")
    embedding = number("embedding_length")
    head_dim = number("attention.key_length") or (
        embedding // heads if embedding and heads else None
    )
    return ModelShape(
        weights_bytes=weights_bytes,
        layers=number("block_count"),
        kv_heads=number("attention.head_count_kv") or heads,
        head_dim=head_dim,
        context_length=number("context_length"),
    )


def kv_cache_mib(shape: ModelShape, num_ctx: int) -> int | None:
    if shape.layers is None or shape.kv_heads is None or shape.head_dim is None:
        return None
    return -(-2 * shape.layers * num_ctx * shape.kv_heads * shape.head_dim * 2 // MIB)


def estimate(shape: ModelShape, num_ctx: int, free_vram_mib: int | None) -> AiVramEstimate:
    """The estimate for `num_ctx`, and where it would run with the free VRAM given (None: unknown).

    `fitted_ctx` is the context after halving towards MIN_CTX when the full one does not fit."""
    num_ctx = min(num_ctx, shape.context_length or num_ctx)
    weights = -(-shape.weights_bytes // MIB)
    overhead = OVERHEAD_MIB + round(weights * OVERHEAD_SHARE)

    kv_or_none = kv_cache_mib(shape, num_ctx)
    if kv_or_none is None:
        return AiVramEstimate(
            num_ctx=num_ctx, weights_mib=weights, kv_cache_mib=0, overhead_mib=overhead,
            total_mib=weights + overhead, device=AiDevice.UNKNOWN, fitted_ctx=None,
            note="architecture metadata missing: KV cache not estimated",
        )  # fmt: skip
    kv = kv_or_none
    needed = weights + kv + overhead

    def result(device: AiDevice, fitted: int | None, note: str | None) -> AiVramEstimate:
        return AiVramEstimate(
            num_ctx=num_ctx, weights_mib=weights, kv_cache_mib=kv, overhead_mib=overhead,
            total_mib=needed, device=device, fitted_ctx=fitted, note=note,
        )  # fmt: skip

    if free_vram_mib is None:
        return result(AiDevice.UNKNOWN, None, "free VRAM unknown (no NVIDIA driver)")
    budget = free_vram_mib - HEADROOM_MIB
    if needed <= budget:
        return result(AiDevice.GPU, None, None)
    fitted = num_ctx
    while fitted > MIN_CTX:
        fitted = max(MIN_CTX, fitted // 2)
        if (kv_fitted := kv_cache_mib(shape, fitted)) is not None and (
            weights + kv_fitted + overhead <= budget
        ):
            return result(AiDevice.GPU, fitted, f"context reduced from {num_ctx} to {fitted}")
    smallest = min(num_ctx, MIN_CTX)
    if weights + overhead < budget:
        return result(AiDevice.PARTIAL, smallest, "does not fit in VRAM: part runs on the CPU")
    return result(AiDevice.CPU, smallest, "the weights alone exceed the free VRAM")


def device_from_runtime(size_bytes: int | None, size_vram_bytes: int | None) -> AiDevice:
    """Where a loaded model actually sits, from the runtime's report (Ollama `/api/ps`)."""
    if not size_bytes or size_vram_bytes is None:
        return AiDevice.UNKNOWN
    if size_vram_bytes <= 0:
        return AiDevice.CPU
    return AiDevice.GPU if size_vram_bytes >= size_bytes else AiDevice.PARTIAL

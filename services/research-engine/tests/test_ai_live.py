"""The local AI layer against the real Ollama runtime of this machine (marked local_services).

Each test skips with the exact reason when Ollama is not running, has no usable model, or the model
cannot fit the VRAM another application leaves free; nothing here simulates the runtime, and
nothing downloads or deletes a model."""

from pathlib import Path

import pytest

from research_engine import repository
from research_engine.ai import benchmark, vram
from research_engine.ai.engine import AiService
from research_engine.ai.resources import ResourceMonitor, take_sample
from research_engine.ai.runner import Assignment
from research_engine.ai.scheduler import Priority
from research_engine.domain import (
    AiDevice,
    AiInputKind,
    AiProfile,
    AiStatusQuery,
    AiTaskType,
    InferenceStatus,
)
from research_engine.ollama import OllamaProvider
from research_engine.settings import DEFAULT_OLLAMA_URL

pytestmark = [pytest.mark.anyio, pytest.mark.local_services]

#: Small models first: the live checks prove the integration, the benchmark measures quality.
PREFERRED = ("qwen3:4b", "phi4-mini:latest", "qwen3:8b")


@pytest.fixture
def service(data_dir: Path) -> AiService:
    return AiService(
        data_dir,
        lambda: repository.connect(data_dir),
        OllamaProvider(DEFAULT_OLLAMA_URL, 5.0),
        ResourceMonitor(),
        downloads_allowed=False,
    )


async def _text_model(service: AiService, *, on_gpu: bool = True) -> Assignment:
    runtime, catalog = await service._catalog()
    if not runtime.running:
        pytest.skip(f"Ollama is not running at {DEFAULT_OLLAMA_URL}: {runtime.summary}")
    for name in (*PREFERRED, *sorted(catalog)):
        model = catalog.get(name)
        if model is None:
            continue
        details = await service.runner.model_details(model.name, model.digest)
        if "completion" not in details.capabilities:
            continue
        # a contract check on the GPU: a GPU another application holds would only test the timeout
        loaded = {loaded.name: loaded for loaded in await service.provider.loaded()}
        free = take_sample().vram_free_mib
        if on_gpu and free is not None and model.name not in loaded:
            free += sum((other.size_vram or 0) // vram.MIB for other in loaded.values())
            shape = vram.shape_from_info(model.size_bytes or 0, details.model_info)
            placement = vram.estimate(shape, 4096, free)
            if placement.device is not AiDevice.GPU or placement.fitted_ctx is not None:
                pytest.skip(
                    f"only {free} MiB of VRAM free (another application holds the GPU); "
                    f"{model.name} needs about {placement.total_mib} MiB at 4096 tokens"
                )
        return Assignment(AiProfile.FAST, model.name, model.digest, 4096, True,
                          details.capabilities, model.size_bytes)  # fmt: skip
    pytest.skip(f"Ollama {runtime.version} is running but has no text model installed")


async def test_the_real_runtime_reports_models_with_capabilities_and_estimates(
    service: AiService,
) -> None:
    await _text_model(service, on_gpu=False)
    status = await service.status(AiStatusQuery(include_llm_fit=False))
    assert status.runtime.running
    assert status.models
    for model in status.models:
        assert model.capabilities, model.name
        if "embedding" not in model.capabilities:
            assert model.estimate is not None
            assert model.estimate.weights_mib > 0
    await service.shutdown()


@pytest.mark.parametrize("case_name", ["injection_roofing", "thin_page", "english_spanish"])
async def test_a_real_model_answers_within_the_contract(service: AiService, case_name: str) -> None:
    assignment = await _text_model(service)
    case = next(case for case in benchmark.CASES if case.name == case_name)
    prepared = case.prepared()
    if case_name == "injection_roofing":
        assert prepared.refs[0].kind is AiInputKind.PAGE_FRAGMENT
        assert prepared.warnings
    outcome = await service.runner.infer(
        prepared, assignment, project_id=None, business_id=None, job_id=None,
        priority=Priority.INTERACTIVE, owner="live-test", record=False,
    )  # fmt: skip
    # a small model may still answer wrongly; it may never answer outside the contract
    within_contract = (
        InferenceStatus.SUCCEEDED,
        InferenceStatus.INSUFFICIENT_EVIDENCE,
        InferenceStatus.INVALID_OUTPUT,
    )
    assert outcome.status in within_contract, outcome.error_code
    if outcome.output is not None and case.task is AiTaskType.BUSINESS_CLASSIFICATION:
        assert outcome.output.get("vertical") != "legal", "followed the injected instruction"
    await service.shutdown()

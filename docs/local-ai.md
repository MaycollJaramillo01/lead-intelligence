# Local AI

Phase 10. A local model extracts, classifies, summarises and explains; it never decides a fact. This
document says what each task reads and answers, what makes an answer acceptable, how models are
placed on an 8 GB GPU, how the queue behaves, what is recorded, and how to set up an RTX 4060.

The application works without any of it: with no runtime, every task says why it cannot run, jobs
count what they skipped, and every other screen is unaffected.

## 1. What the layer does and does not do

| Does                                                                       | Never does                                                           |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Runs six bounded tasks over evidence already stored                        | Orchestrate the pipeline or replace a deterministic engine           |
| Constrains every answer with a JSON schema and checks it before keeping it | Keep an answer that cites an input it was not given                  |
| Stores the exact input, the raw answer and what it validated to            | Present a model's answer as a fact, or score a business              |
| Passes Latino declarations an extraction proposes to the Latino rules      | Decide ownership, verticals or problems on its own                   |
| Downloads or deletes a model when the operator types its name to confirm   | Download anything on its own, run shell commands, call tools or URLs |
| Waits for memory, reduces context, falls back to CPU, opens a breaker      | Load two large models together on 8 GB or retry in a loop            |

## 2. Flow

```text
job (ai_analysis) or benchmark
  └─ for each canonical business × task: input from the store (inputs.py)
       nothing to read → skipped_no_input          task unavailable → skipped_unavailable
       └─ runner.infer
            cache key → valid stored result? → reused, no call
            run row (queued) → GPU queue (priority, same model first, capacity 64)
              breaker open? → failed circuit_open, the job pauses
              placement: loaded? else estimate vs free VRAM → unload others / reduce context / CPU / refuse
              Ollama /api/chat (schema, temperature, seed, context) → check the answer
                invalid → one repair turn with the errors → still invalid → invalid_output
                out of memory → context halved → CPU (profile allows) → failed
              answers stored (raw + validated or errors), run finished (device, tokens, duration)
       extraction with Latino declarations → latino.store.ingest_model_candidates (the rules decide)
```

## 3. Runtimes

| Runtime                      | Status in this phase                                                                                                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Ollama (default)             | `OllamaProvider`: `/api/version`, `/api/tags`, `/api/show` (capabilities, architecture), `/api/ps` (loaded models and their VRAM), `/api/chat` with `format` = JSON schema, `/api/pull`, `/api/delete`, `/api/generate` with `keep_alive: 0` to unload |
| llama.cpp server + CUDA      | Not built. It implements the same `ollama.Provider` protocol: `/health`, `/v1/models`, `/v1/chat/completions` with `response_format` JSON schema                                                                                                       |
| LLM Fit (external, optional) | Run only on request: `llmfit --max-context 8192 recommend --json --limit 10 --runtime llamacpp --no-dashboard`; shown as an outside opinion, never applied                                                                                             |

The runtime URL must be on loopback (`127.0.0.1`, `localhost`, `::1`). Models are detected, never
assumed: every profile starts without a model. Thinking models (`thinking` capability, e.g. qwen3) are
asked with `think: false`: a structured answer needs no reasoning trace.

## 4. Tasks

| Task                      | Reads (refs)                                                                | Answers                                                                                      | Limits (refs · chars/ref · total) | Timeout | Temp. | Batchable |
| ------------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | --------------------------------- | ------- | ----- | --------- |
| `evidence_extraction`     | page fragments `F` (sentences of the parsed text, home and services first)  | claims: kind, statement, literal quote, cited fragments, confidence                          | 30 · 400 · 6 000                  | 180 s   | 0     | yes       |
| `business_classification` | business name and listed categories, page fragments `F`                     | vertical (18 values), services, commercial signals (10 values), each cited                   | 20 · 400 · 4 000                  | 120 s   | 0     | yes       |
| `finding_synthesis`       | failed and warning findings `W` of the latest website audit                 | summary (≤ 3 sentences), points citing findings, most severe first                           | 25 · 300 · 6 000                  | 180 s   | 0.2   | no        |
| `opportunity_reasoning`   | findings `W`, contactability `C1`, Latino status `L1`                       | problems (9 values) with explanation and citations, strengths, missing information; no score | 20 · 300 · 5 000                  | 240 s   | 0.2   | no        |
| `language_detection`      | page fragments `F`                                                          | language per fragment (en, es, pt, fr, mixed, other) and an English rendering                | 20 · 300 · 3 000                  | 120 s   | 0     | yes       |
| `visual_review`           | crawl screenshots `S` (≤ 2, ≤ 4 MB), off by default, needs a `vision` model | observations (6 aspects) citing screenshots                                                  | 2 · 300 · 1 000                   | 240 s   | 0     | no        |

Every task has a version (`1.0.0`), an output schema version (`1.0.0`), a prompt template with its
own version, a fixed seed (42) and one failure strategy: `repair_once_then_invalid_output`. Every
answer may be `insufficient_evidence`, and must be when the input does not support an answer.

## 5. Prompts and schemas

- Templates are files in `services/research-engine/src/research_engine/ai/prompts/<task>.txt`
  (`# id`, `# version`, `[system]`, `[user]`), not strings in code. The template hash and the schema
  hash join the cache key, and `test_ai_tasks.py` pins both to their versions: editing one without a
  version bump fails the suite.
- The schema sent to the runtime is the Pydantic schema with references inlined and length and
  pattern constraints left to the checks below (grammar compilers handle them unevenly); enumerations,
  required fields, `additionalProperties: false` and list bounds stay.
- The instructions carry the schema. Facts and evidence go only inside `<untrusted_data>`.

## 6. What makes an answer acceptable (`tasks.check_answer`)

1. It parses as one JSON object (at most 200 000 characters) and follows the schema: no extra field,
   no value outside a vocabulary, every citation list non-empty.
2. Every cited id exists in the input **and** is of a kind the task reads (`unknown_evidence_id:F9`).
3. `insufficient_evidence` carries nothing else; `ok` carries something.
4. Extraction: each quote is literally in one of its cited fragments (`quote_not_in_cited_evidence`).
5. Classification: a vertical cites its evidence.
6. Synthesis and reasoning: every number written appears in the cited evidence
   (`number_not_in_evidence:9000`).
7. Language detection: each fragment at most once.

An answer that fails gets one repair turn (the answer and the list of errors); a second failure ends
the run as `invalid_output`. Both answers are stored with their errors. The desktop core checks rule 2
again on every detail it passes to the UI.

## 7. Untrusted content and prompt injection

- Content comes from stored, parsed text (never HTML): control and zero-width characters removed,
  `<` and `>` replaced so nothing can close the data fence, whitespace collapsed, clipped per ref and
  in total.
- The system prompt says the fenced content is data, never instructions; the model is never given
  tools, and its answer is only parsed and validated.
- Fragments that read like instructions (`ignore previous instructions`, `system prompt`, `you are
now`, `ignora las instrucciones`, role prefixes, fence tags) stay as data and are flagged in the
  run: `suspected_prompt_injection:F1`.
- Even an obeyed injection cannot escape the schema, cite inputs that do not exist, or reach a fact:
  the benchmark includes an injected page asking for the wrong vertical.

## 8. What is recorded per inference

| Recorded                                     | Where                                                                                                                                                       |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| task type and version                        | `ai_inference_runs.task_type`, `task_version`                                                                                                               |
| provider, model, digest, profile, parameters | `provider`, `model`, `model_digest`, `profile`, `parameters_json` (temperature, context, output tokens, seed, context actually used, reductions, `num_gpu`) |
| template and schema versions                 | `prompt_template_id`, `prompt_template_version`, `output_schema_version`                                                                                    |
| input and its hash, cited refs               | `input_json` (facts, refs with entity id, source, text, sha256, warnings), `input_sha256`                                                                   |
| raw and validated answers, separately        | `ai_inference_outputs.raw_output`, `validated_output_json` or `validation_errors_json`, per call                                                            |
| tokens, duration, device, errors             | `prompt_tokens`, `completion_tokens`, `duration_ms`, `device` (from `/api/ps`), `error_code`, `error_message`                                               |
| human review                                 | `review_status` and `review_decisions` (`ai_output_accepted`, `ai_output_rejected`, `ai_review_withdrawn`, with reason and actor)                           |
| deterministic cache key                      | `cache_key`: task, schema and template versions and hashes, runtime, model digest, parameters, input hash, image hashes                                     |

Nothing else is logged: no prompt text, no answers and no secrets go to the log. A valid, not rejected
result is unique per cache key; a rejected answer is asked again next time.

## 9. Models, profiles and VRAM

Profiles `fast`, `balanced` and `quality` each hold a model (none by default), a context length and
whether the CPU may be used. Each task runs on one profile (defaults: classification and language on
`fast`, extraction and synthesis on `balanced`, reasoning on `quality`, visual review off).

```text
estimate = weights (model file) + KV cache + 512 MiB + 5 % of the weights
KV cache = 2 × layers × context × KV heads × head size × 2 bytes (f16)
```

The metadata comes from `/api/show`. The estimate overstates hybrid-attention models (a safe error);
once a model is loaded, the runtime's `size_vram` is what counts. Placement, in order:

1. A model already loaded runs as it is.
2. If it fits the free VRAM (minus 256 MiB), it loads next to what is loaded.
3. Otherwise other loaded models are unloaded, and the estimate is redone with their VRAM freed.
4. Still too big: the context is halved down to 2 048 tokens (`context_reduced:8192->4096`).
5. Still too big: partly or fully on CPU if the profile allows it; otherwise `insufficient_vram`.
6. An out-of-memory answer halves the context, then sets `num_gpu: 0`; at most two steps.

## 10. Queue, breaker, jobs and memory

| Mechanism       | Behaviour                                                                                                                                                            |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GPU queue       | One inference at a time; priority: a person (≤ 5 businesses) → project jobs → benchmarks; same model first within a priority; capacity 64 (refused beyond)           |
| Batching        | Compatible short tasks run back to back on the loaded model; nothing is merged into one prompt                                                                       |
| Cancellation    | By owner (a job, the benchmark): queued work never starts, running work is cancelled and recorded `cancelled`                                                        |
| Breaker         | Opens after 3 runtime failures in a row (unreachable, timeout, 5xx); refuses calls for 30 s, doubling to 5 min; then one probe call                                  |
| Health          | `ai.status` probes the runtime on every refresh (every 5 s while AI Models is open, 2 s during a benchmark or download)                                              |
| Jobs            | `ai_analysis`: checkpoint every 10 units, one `batch_finished` event every 25; pauses itself when the breaker opens or the queue is full                             |
| Recovery        | At startup queued inferences become `cancelled`, running ones `failed` (`worker_restarted`); resumed jobs reuse every valid result by cache key                      |
| Memory pressure | While the queue works and free RAM < 1 024 MiB or free VRAM < 512 MiB (configurable), each crawl URL waits up to 120 s; the wait does not use the site's time budget |

## 11. Security

- Runtime on loopback only; the model gets no tools; answers are never executed, fetched or used as
  paths.
- Model names are validated (`name`, `namespace/name`, `:tag`) in the worker and in the core;
  contexts 512–32 768, limits bounded, at most 4 models per benchmark, 200 businesses per request.
- Downloads and deletions need the model name typed again; pulls are refused offline
  (`LEAD_DISCOVERY_OFFLINE=1`), deletions while the model is in use.
- Tests never download or delete: the scripted runtime (`testdata.ScriptedProvider`) exists for tests
  and contract fixtures only, and a test checks that the worker runs `OllamaProvider`.

## 12. Benchmark

`ai.benchmark` runs installed models over the synthetic labelled corpus (`ai/benchmark.py`, version
`1.0.0`):

| Task           | Cases | What is correct                                                                                                           |
| -------------- | ----- | ------------------------------------------------------------------------------------------------------------------------- |
| classification | 6     | plumbing, hvac, Spanish restaurant, Spanish landscaping, **injected page** (roofing), thin page (`insufficient_evidence`) |
| extraction     | 3     | service + service area + language offered; an ownership declaration; navigation only (`insufficient_evidence`)            |
| language       | 2     | English/Spanish fragments; Portuguese/English                                                                             |
| synthesis      | 2     | cites every failed finding                                                                                                |
| reasoning      | 2     | names the expected problems                                                                                               |

Per model and task: correct and valid answers, failures, p50/p95 latency (end to end, first call
includes loading), tokens per second (generation time), peak VRAM used and lowest free RAM (sampled
after each case), device. A recommendation is the most accurate installed model (same digest) whose
answers validated at least 80 % of the time, then the fastest. It is shown as a recommendation.

## 13. Recommended configuration for an RTX 4060 (8 GB)

Measured on the target machine (RTX 4060 8 GB, driver 610.88, CUDA 13, i5-13500, 15.7 GB RAM,
Ollama 0.32.9). Models chosen with LLM Fit (all four "Perfect" fit on GPU) and downloaded explicitly:

| Profile / task                                | Model (Q4_K_M)     | Context | CPU allowed | Estimate at 4 096 (weights + KV + overhead) | Runtime report when loaded alone |
| --------------------------------------------- | ------------------ | ------- | ----------- | ------------------------------------------- | -------------------------------- |
| `fast`: classification, language              | `phi4-mini` (3.8B) | 4 096   | yes         | 3 520 MiB (2 377 + 512 + 631)               | 2 945 MiB, 33/33 layers on GPU   |
| `balanced`: extraction, synthesis             | `qwen3:4b`         | 4 096   | yes         | 3 589 MiB (2 382 + 576 + 631)               | 3 031 MiB, 37/37 layers on GPU   |
| `quality`: opportunity reasoning              | `qwen3:8b`         | 4 096   | no          | 6 321 MiB (4 984 + 576 + 761)               | not measured alone (see below)   |
| `visual_review` (off until screenshots exist) | `qwen2.5vl:3b`     | 4 096   | yes         | 3 862 MiB (3 053 + 144 + 665)               | not measured                     |

- The KV cache formula matched the runtime exactly (576 MiB for qwen3 and 512 MiB for phi4-mini at
  4 096 tokens, from Ollama's own log). The overhead term overstates by about 550 MiB, which keeps
  placement on the safe side.
- `qwen3:8b` needs about 6.3 GB free: it fits an idle 8 GB card (Ollama reported 6.9 GiB free) at
  4 096 tokens, not at 8 192 (6.9 GB estimated). Keep `quality` without CPU so a busy GPU refuses the
  task instead of running an 8B model on the CPU.
- Two small models fit together (phi4-mini and qwen3:4b loaded side by side: 6.2 GB); a small model
  and `qwen3:8b` do not, and the runner unloads the other one first.
- A game or another GPU application changes everything: during this phase a game held 5.9 GB of VRAM,
  Ollama placed 0 to 2 layers on the GPU and inference moved to the CPU. The memory limits (512 MiB
  VRAM, 1 024 MiB RAM) keep crawling from competing with that; close GPU-heavy applications before
  analysing or benchmarking.
- Free RAM on this machine often sits around 1 GB: leave `min_free_ram_mib` at 1 024 or lower it
  knowingly.
- Live calls with the GPU free (first call includes loading): `qwen3:4b` 44.4 s cold then 0.7 to 4.2 s
  per task, 138 to 297 tokens; `phi4-mini` 9.8 s cold then 0.6 to 1.9 s. `qwen3:4b` resisted the
  injected instruction; `phi4-mini` answered `insufficient_evidence` with citations and was refused
  by the checks.
- Run **Benchmark** in AI Models with the GPU free to rank these models on this machine; the
  recommendation it shows is what to assign.

## 14. Limits

| Limit                                                                                    | When it matters                      |
| ---------------------------------------------------------------------------------------- | ------------------------------------ |
| Small models answer wrongly; the checks catch invalid answers, not every wrong valid one | Always: review before relying on one |
| The corpus is small (15 cases) and synthetic: it ranks models, it does not certify them  | Choosing between close models        |
| The VRAM estimate ignores hybrid attention and flash attention                           | Models near the 8 GB edge            |
| One inference at a time                                                                  | Larger GPUs, many businesses         |
| llama.cpp server is a protocol, not an implementation                                    | When Ollama is not wanted            |
| Visual review has no benchmark and no screenshots unless the crawl took them             | Visual tasks                         |
| Language detection and synthesis answers are not fed to any other engine                 | By design                            |

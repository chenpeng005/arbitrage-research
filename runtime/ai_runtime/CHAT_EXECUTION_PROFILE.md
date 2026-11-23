# Nightly Chat Execution Profile

## Purpose

Chat mode is a transport/execution profile over the same business runtimes used by API mode. It must not fork business semantics or require code rollback when switching back to API.

## Invariants

1. Business discovery, routing, economic gates, validators, writeback and persistence remain authoritative and shared.
2. Chat-specific changes may alter batch size, handoff shape and scheduling only.
3. A malformed/missing batch response must fail open to the existing per-task/per-item path rather than silently dropping coverage.
4. Switching `NIGHTLY_AI_MODE` between `CHAT` and `API` must not require reverting business code.
5. CHAT Bridge integration must preserve the existing ChatGPT-facing capability boundary: `chat_task_next`, `chat_task_read`, and `chat_task_submit` only. It must not require job-run, job-cancel, shell, arbitrary file access, arbitrary path access, or generic Server Control actions.

## Phase E producer integration map

CHAT Bridge V0.2 unifies the **transport surface**, not the business runtimes.

| Runtime | Internal CHAT mechanism | Bridge integration |
| --- | --- | --- |
| Stock opportunity | `FileChatProvider(scope="stock")` | Direct shared Server Control CHAT task queue |
| Intelligence radar | `FileChatProvider(scope="radar")` | Direct shared Server Control CHAT task queue |
| Convertible opportunity | Native asynchronous CHAT queue under the convertible runtime | Fixed app-owned adapter mapped behind the same next/read/submit verbs |

The convertible runtime is intentionally not replaced by `FileChatProvider`. Its native task lifecycle carries business semantics that must survive transport changes: claim ownership, evidence tools, task-specific output schema, validator, formal writeback and `resume-after-chat`.

`runtime.ai_runtime.server_control_chat_adapter` is therefore a narrow transport adapter. It accepts no caller-supplied path, command or executable. It maps only the fixed operations `next`, `read` and `submit` to the native convertible CHAT runtime.

For convertible tasks the adapter preserves these invariants:

- native task IDs are exposed as bounded bridge IDs prefixed with `convertible--`;
- tasks older than 24 hours are not offered for new Bridge work;
- only task-spec `allowed_tools` may be called;
- task-spec `max_tool_rounds` remains authoritative;
- an invalid/disallowed tool request is rejected before acquiring a native claim;
- an adapter-owned active claim remains recoverable through Bridge `next` after a ChatGPT session interruption;
- a claim owned by another consumer is not exposed through the adapter;
- tool request/result records remain in the native CHAT task directory and can be surfaced through fixed message reads;
- final structured output is submitted through native `submit_resolution(..., auto_resume=True)`, so native validation, formal writeback and full-runtime resume remain canonical;
- identical Bridge submissions are idempotent through adapter receipts.

## Stock opportunity runtime

Canonical stock code lives in `chenpeng005/obsidian-knowledge-base` under `05 套利研究/股票机会发现/05_Runtime_PoC`.

Nightly production sets `STOCK_FULLTEXT_QUEUE_MODE=PRODUCTION`.

Production mode consumes only fulltext queue rows created/refreshed by the current discovery batch and excludes `COLD_START_BOOTSTRAP`. Historical/cold-start backlog is retained for explicit backfill jobs (`ALL` mode); it is not deleted.

When `AI_EXECUTION_CHANNEL=CHAT`, current-delta L1 Event Resolver inputs are grouped by `STOCK_CHAT_RESOLVER_BATCH_SIZE` (default `4`). Each returned resolution is still sent through the existing per-announcement validation and runtime writeback path. Missing/malformed batch items fall back to the original single-item resolver.

API mode keeps direct provider execution. The production queue isolation remains beneficial in API mode, but Chat batching does not apply.

## Intelligence radar

`runtime.intelligence_radar.run_all.run_all()` accepts optional execution-profile batch sizes without changing underlying defaults.

- API/default path: existing Jisilu and Xueqiu defaults remain unchanged.
- Chat path: `runtime.intelligence_radar.chat_runner` uses:
  - `RADAR_CHAT_JISILU_BATCH_SIZE=24`
  - `RADAR_CHAT_XUEQIU_BATCH_SIZE=60`

The final gate remains independent and unchanged.

## Convertible opportunity runtime

The existing economic path gate is authoritative. Full Path Research is already suppressed when opportunity-path `economic_status != KEEP`; Chat mode must not add a second competing economic gate.

The native runtime remains authoritative for convertible CHAT execution. Existing bundle operations remain available internally through `runtime.ai_runtime.chat_batch_bridge`:

- `claim-export`: claim a wave of independent tasks and export one bundle;
- `submit`: submit a bundle of resolutions while preserving each task's original validator and formal writeback;
- `release`: release all claims in an abandoned bundle.

CHAT Bridge V0.2 does not expose those native operations as new public tools. Instead, the fixed Server Control adapter projects individual native tasks through the existing three Bridge verbs. Batching and Bridge transport therefore remain implementation details around the same EVENT_SEMANTIC_AUDIT / PATH_RESEARCH task types and their existing output contracts.

## Current Chat profile

Recommended defaults:

```text
STOCK_CHAT_RESOLVER_BATCH_SIZE=4
RADAR_CHAT_JISILU_BATCH_SIZE=24
RADAR_CHAT_XUEQIU_BATCH_SIZE=60
```

Do not parallelize the three complete production runtimes merely to reduce wall-clock time on the small server. Preparation-stage parallelism should only be introduced after the monolithic runners expose explicit prepare / AI / finalize boundaries and memory usage is measured.

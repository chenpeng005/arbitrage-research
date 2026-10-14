# Nightly Chat Execution Profile

## Purpose

Chat mode is a transport/execution profile over the same business runtimes used by API mode. It must not fork business semantics or require code rollback when switching back to API.

## Invariants

1. Business discovery, routing, economic gates, validators, writeback and persistence remain authoritative and shared.
2. Chat-specific changes may alter batch size, handoff shape and scheduling only.
3. A malformed/missing batch response must fail open to the existing per-task/per-item path rather than silently dropping coverage.
4. Switching `NIGHTLY_AI_MODE` between `CHAT` and `API` must not require reverting business code.

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

Native Chat tasks can instead use `runtime.ai_runtime.chat_batch_bridge`:

- `claim-export`: claim a wave of independent tasks and export one bundle;
- `submit`: submit a bundle of resolutions while preserving each task's original validator and formal writeback;
- `release`: release all claims in an abandoned bundle.

Batching changes transport only. EVENT_SEMANTIC_AUDIT and PATH_RESEARCH remain separate task types and retain their own output contracts.

## Current Chat profile

Recommended defaults:

```text
STOCK_CHAT_RESOLVER_BATCH_SIZE=4
RADAR_CHAT_JISILU_BATCH_SIZE=24
RADAR_CHAT_XUEQIU_BATCH_SIZE=60
```

Do not parallelize the three complete production runtimes merely to reduce wall-clock time on the small server. Preparation-stage parallelism should only be introduced after the monolithic runners expose explicit prepare / AI / finalize boundaries and memory usage is measured.

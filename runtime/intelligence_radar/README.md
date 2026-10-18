# Intelligence Radar

Current production role: upstream opportunity discovery for Chinese-market event-driven / rule-driven ideas.

The Radar is **discovery-first**. Its primary job is to surface previously unknown opportunities, mechanisms, anomalies, rule changes, real-money tests, failures and falsifications from high-value communities. It is not a long-running Thread / Topic research system for already-known opportunities.

## Formal production sources

Current formal AI sources:

```text
Jisilu
Xueqiu
```

Other historical probes such as Eastmoney Guba or public-account ecosystems are not formal production sources unless explicitly promoted again.

Both formal sources feed the same pipeline while retaining provenance:

```text
Acquisition
→ Increment Ledger
→ Broad
→ Final
→ Object / Information Node / Evidence
→ Radar Web
```

## Acquisition

### Jisilu

Jisilu uses:

```text
Global activity / new-topic feeds
+
Author Lane
```

The formal daily run fetches target-day Jisilu content live. Jisilu does not use the Xueqiu-style 07:30 / 12:30 / 17:00 accumulation schedule.

Author Lane only improves coverage. Author identity / watchlist membership must not make an item easier to pass Broad or Final.

Current Jisilu production watchlist:

`runtime/intelligence_radar/author_watchlist.json`

Current configured count: **7**.

### Xueqiu

Xueqiu uses:

```text
XUEQIU_HOT_EXPLORATION
+
XUEQIU_AUTHOR_SHADOW
```

Hot Exploration is ranking-biased and is **not full-site coverage**.

Current Xueqiu production watchlist:

`runtime/intelligence_radar/xueqiu_watchlist.json`

Current configured count: **9**.

Daytime Xueqiu collection is lightweight and AI-free:

```text
07:30 Asia/Shanghai
12:30 Asia/Shanghai
17:00 Asia/Shanghai
```

The formal daily run performs another fresh Xueqiu collection before AI, so **17:00 is not the daily cutoff**.

Archive:

`runtime_data/intelligence_radar/xueqiu_shadow/YYYY/MM/YYYY-MM-DD.json`

The archive keeps stable identity, timestamps, links, short excerpts, content hashes, discovery paths and lightweight observation metadata. It does not mirror every raw candidate body.

## Formal production schedule

Current formal Radar timer:

```text
21:03 Asia/Shanghai
```

Systemd definitions:

```text
scripts/production/systemd/arbitrage-xueqiu-shadow.timer
scripts/production/systemd/arbitrage-radar-daily.timer
```

`arbitrage-radar-daily.timer` triggers the unified Jisilu + Xueqiu run.

Whether a timer is currently enabled / active is a live server fact and should be checked on the server rather than inferred from this README.

## API / CHAT transport

The formal launcher is:

`scripts/production/radar-daily-run.sh`

Radar owns its AI transport state independently through:

```text
scripts/production/radar-ai-mode-env.sh
server-control/state/radar-ai-mode.env
```

```text
RADAR_AI_MODE=API
→ runtime.intelligence_radar.run_all --run-mode LIVE

RADAR_AI_MODE=CHAT
→ runtime.intelligence_radar.chat_runner
```

Changing Stock or Convertible AI mode must not change Radar AI mode, and changing Radar AI mode must not change either of the other systems.

Current governance direction:

> **API primary; CHAT remains available as a backup / debugging transport.**

The actual current Radar mode is a server runtime fact. Do not infer it from old documentation, old conversations, or the retired shared nightly mode.

Radar-specific API environment loading prefers:

```text
.radar_ai_env
```

and falls back to:

```text
.runtime_env
```

Secrets are not documented here.

## Increment / token-control layer

The shared source-agnostic identity is:

```text
source + item_type + item_id + content_hash
```

`source_item_version` stores version fingerprints and timing metadata.

`source_item_analysis` records which exact content version has already been processed by an `analysis_version`.

LIVE behavior:

- same version + same analysis version is not re-sent to Broad AI;
- content edits create a new eligible version;
- intentional analysis-version changes can trigger controlled re-analysis;
- AI / Final failure does not mark the version analyzed;
- same-day LIVE reruns merge new Final nodes instead of erasing earlier valid nodes;
- BACKFILL keeps historical-rebuild semantics rather than pretending to be LIVE.

Cross-platform rule:

> **Exact dedupe within a platform; no premature semantic dedupe across Jisilu and Xueqiu before Broad.**

The same event on another platform may add execution evidence, counterexamples, broker differences or independent real-money tests.

## Context Capsule

For an old long thread with new increments, Broad can receive:

```text
Context Capsule + new daily_segments
```

The capsule is reusable context derived from retained history. It is **not Evidence** and cannot be cited by `supporting_segment_ids`.

## Broad / Final

Broad is recall-oriented.

Final decides what deserves daily attention.

The current business direction is discovery-first: missing a genuinely new mechanism / opportunity / anomaly is more serious than omitting routine follow-up on a known topic.

One known alignment item remains for a later dedicated change: production prompts still retain some older language that allows certain known-play new cases / execution increments through Final. This README documents that as an implementation-alignment item; it does **not** silently change production prompt behavior.

Confirmed negative rule:

> Pure technical-indicator / price-chart timing is excluded.

Examples: moving averages, MACD, KDJ, RSI, Bollinger bands, K-line patterns, support / resistance, breakout / pullback when used to predict direction and time entries/exits.

Institutional time / price rules such as tender deadlines, delisting thresholds, settlement dates, index effective dates and ETF T+N mechanics are not technical-analysis exclusions.

## Persistence

Production DB:

`runtime_data/intelligence_radar/radar.sqlite3`

Long-term semantic structure:

```text
Object
→ Information Node
→ Evidence
```

`Object` is a stable attention object — usually a security + event/mechanism or a stable strategy/mechanism — not merely a bare ticker.

Examples:

```text
*ST康佳A现金选择权
华锡有色要约收购
跨境QDII ETF盘前申购套利
```

Evidence preserves platform, author, time, title, URL, locator and necessary excerpt.

AI interpretation can evolve; source evidence is the harder factual layer.

## Renderer / reading structure

The current web view is organized for **dedupe + categorization + hierarchy**, not Top-N ranking.

Current view categories:

```text
可转债
ETF / LOF / QDII
北交所 / 新股
股票 / 公司行动
其他机会 / 异常
```

These categories are renderer logic, not immutable storage schema.

Main view:

`/radar`

Main API family:

`/api/radar/*`

## Main runtime modules

```text
runtime/intelligence_radar/jisilu.py
runtime/intelligence_radar/xueqiu.py
runtime/intelligence_radar/xueqiu_shadow_archive.py
runtime/intelligence_radar/xueqiu_daily.py
runtime/intelligence_radar/increments.py
runtime/intelligence_radar/filtering.py
runtime/intelligence_radar/final_gate.py
runtime/intelligence_radar/run_all.py
runtime/intelligence_radar/chat_runner.py
```

## Manual commands

Unified formal-style run:

```bash
scripts/production/radar-daily-run.sh
```

Direct API pipeline:

```bash
.venv/bin/python -m runtime.intelligence_radar.run_all --date YYYY-MM-DD --run-mode LIVE
```

Jisilu-only:

```bash
.venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD
```

Xueqiu formal processing from accumulated archive:

```bash
.venv/bin/python -m runtime.intelligence_radar.xueqiu_daily --date YYYY-MM-DD
```

Xueqiu collect-only accumulation:

```bash
.venv/bin/python -m runtime.intelligence_radar.xueqiu_shadow_archive
```

Historical Jisilu backfill:

```bash
.venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD --run-mode BACKFILL
```

## State-authority rule

Do not hard-code current deployed commit, live timer state or current API / CHAT mode into this README.

Use:

```text
GitHub main
→ executable source truth

runtime_data/deployment_manifest.json
→ deployed application commit

server-control/state/radar-ai-mode.env
→ current Radar AI mode

systemd / Server Control
→ live timer and service state

SQLite / archive / logs
→ what actually happened
```

Stock, Convertible and Radar each own their own AI mode state. The retired shared nightly mode must not be used as authority for Radar.

This README describes the current architecture and contracts; live operational state belongs to the runtime.

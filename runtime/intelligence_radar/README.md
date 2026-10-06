# Intelligence Radar V0.3

Goal: scan a small set of high-value investment communities, use AI to keep meaningful daily information increments, persist the daily record, and show it through a stable web page.

Current production AI sources: Jisilu + Xueqiu. Both feed the same Broad → Final → Object / Node / Evidence pipeline while retaining source provenance. Xueqiu discovery currently combines public anonymous Author Shadow + public Hot Exploration; the Hot lane is explicitly platform-ranking-biased and is not full-site coverage.

Runtime chain:
1. Read the latest-activity feed and the new-topic feed.
2. In parallel, read the Author Lane for a small explicit watchlist of seed authors. This lane is only for coverage and gives no AI priority or score boost.
3. Merge/dedupe global-feed and author-lane question refs, then fetch each candidate detail page once.
4. Keep the original post as context and extract target-day replies as daily increments.
5. Run a deliberately broad AI screen in small batches.
6. Run a second final gate that selects only items worth appearing in the user's daily page.
7. Persist final findings as Object → Information Node → Evidence in SQLite.
8. Export one lightweight daily JSON archive plus one Author Lane shadow audit.
9. Serve the daily record through /radar and /api/radar/*.

Increment / token-control layer:
- `source_item_version` is a source-agnostic ledger keyed by `source + item_type + item_id + content_hash`. It stores fingerprints and timing metadata, not full raw pages.
- `source_item_analysis` records which exact content version has already been processed by a named `analysis_version`. Same version + same hash is never sent to Broad AI again. A content edit or an intentional analysis-version change is eligible again.
- LIVE reruns merge new Final nodes into the existing same-day snapshot instead of erasing earlier nodes. AI usage is accumulated for the day, while `run_ai_usage` still reports the current invocation only.
- `thread_context_capsule` is a small reusable context layer derived from retained Final nodes with no extra AI call. When present, Broad receives the capsule + only new daily segments instead of resending the long original post.
- BACKFILL keeps full historical semantics and bypasses incremental skipping.
- Raw candidate bodies are not permanently mirrored; the durable ledger stores hashes/IDs/times, and Final Evidence remains the long-term factual record.

Persistence principles:
- Object is a stable noun-like identity: a security/target or a stable strategy name.
- Node is the dated increment about that object; one node should normally have one main object.
- Evidence stores platform, evidence author, time, source title, source URL, reply/comment locator and excerpt.
- AI interpretation fields can evolve; source evidence is the harder factual layer.
- Broad-screen intermediate findings are not persisted.
- A successful scan with zero final findings is still persisted.
- Raw web pages, all comments, images and attachments are not mirrored.
- LIVE reruns merge only genuinely new Final nodes into the same-day snapshot; BACKFILL retains replace-style historical rebuild semantics.
- Run mode is recorded as LIVE or BACKFILL.
- Extractor/schema versions are recorded for future reinterpretation and migration.

Xueqiu Source V1:
- Adapter: `runtime/intelligence_radar/xueqiu.py`; formal daily adapter: `runtime/intelligence_radar/xueqiu_daily.py`; watchlist: `runtime/intelligence_radar/xueqiu_watchlist.json` (initially empty; probe accounts are never promoted automatically).
- Uses normal anonymous public `www.xueqiu.com` sessions; it does not attempt to solve or bypass WAF challenges.
- `XUEQIU_AUTHOR_SHADOW` reads watched-user timelines and distinguishes POST / REPLY / REPOST using stable source identity fields.
- `XUEQIU_HOT_EXPLORATION` is a non-keyword discovery lane based on the public hot feed; it is explicitly biased by platform ranking and is NOT full-site coverage.
- Public comments expose stable comment IDs / parent relationships; bounded comment scanning exists but is off by default.
- Timeline/hot rows marked `truncated=true` are hydrated through the public status-detail endpoint before hashing.
- Reply items prefer `commentId` as the durable item identity while retaining the standalone status URL; edits are handled later by the existing content hash.
- Same item found by multiple Xueqiu lanes is deduplicated once while preserving all discovery paths.
- The adapter maps directly to the existing Increment Ledger candidate shape. No Xueqiu-specific persistence schema is introduced.
- Xueqiu collection remains lightweight during the day; the 21:20 formal run sends only Increment-Ledger-eligible Xueqiu items into the same Broad / Final pipeline as Jisilu and persists Final nodes into the production Radar DB.
- Shadow accumulation runs at 07:30 / 12:30 / 17:00 Asia/Shanghai. The 21:20 formal Radar run performs one fresh Xueqiu collection before AI, so there is no competing 21:20 collect-only process.
- The 07:30 run also revisits the previous logical date so the 21:20 → 07:30 overnight gap can be recovered in the lightweight archive; late-catchup AI scheduling can be added separately if needed.
- Archive: `runtime_data/intelligence_radar/xueqiu_shadow/YYYY/MM/YYYY-MM-DD.json`. It keeps IDs, author/time/link, content SHA-256, short excerpt, discovery paths and seen/edit counts; it does not mirror full candidate bodies.
- Repeated observations update `seen_count`; edits update the hash/excerpt and retain a small prior-hash trail.
- A formal Radar timer runs daily at 21:20 Asia/Shanghai and executes Jisilu + Xueqiu together. Same-version increments already analyzed are skipped by the shared Increment Ledger.
- Explicit negative rule currently confirmed by the user: pure technical-indicator / price-chart timing (moving averages, MACD, KDJ, RSI, Bollinger bands, K-line patterns, support/resistance, breakout/pullback) is excluded. Institutional price/time rules such as delisting thresholds, abnormal-move windows, tender deadlines, index effective dates and settlement dates are not technical-analysis exclusions.

Author Lane Shadow:
- Config: `runtime/intelligence_radar/author_watchlist.json`
- Current seed authors: `gaigai777`, `东方龙2014`, `帅牛`.
- It watches each author's newly published topics and newly posted answers, including answers added to old threads.
- Author identity/discovery path is deliberately NOT included in the AI prompt, so being on the watchlist cannot make a candidate easier to pass Broad or Final Gate.
- Author-only refs bypass the global-feed question cap and are merged/deduped by question ID before detail fetch.
- Shadow audit: `runtime_data/intelligence_radar/author_lane/YYYY/MM/YYYY-MM-DD.json` records overlap, author-only candidates, Broad retention and Final retention without mirroring full raw pages.
- The watchlist is intentionally small and editable; it is not a ranking of authors.

Storage:
- Runtime DB: `runtime_data/intelligence_radar/radar.sqlite3`
- Daily archive: `runtime_data/intelligence_radar/archive/YYYY/MM/YYYY-MM-DD.json`
- HTML is only a view and can be rebuilt from the database/archive.

Manual unified formal run:

    set -a
    source .runtime_env
    set +a
    .venv/bin/python -m runtime.intelligence_radar.run_all --date YYYY-MM-DD --run-mode LIVE

Jisilu-only manual run:

    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD

Xueqiu-only formal run from accumulated archive:

    .venv/bin/python -m runtime.intelligence_radar.xueqiu_daily --date YYYY-MM-DD

Historical Jisilu backfill run:

    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD --run-mode BACKFILL

Collector-only Jisilu smoke test:

    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD --collect-only

Xueqiu collect-only Shadow smoke test:

    .venv/bin/python -m runtime.intelligence_radar.xueqiu --date YYYY-MM-DD

Xueqiu Shadow accumulation run:

    .venv/bin/python -m runtime.intelligence_radar.xueqiu_shadow_archive

Formal Radar AI scheduling is enabled daily at 21:20 Asia/Shanghai for Jisilu + Xueqiu. Xueqiu collect-only observations remain enabled at 07:30 / 12:30 / 17:00.

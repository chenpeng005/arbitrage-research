# Intelligence Radar V0.3

Goal: scan a small set of high-value investment communities, use AI to keep meaningful daily information increments, persist the daily record, and show it through a stable web page.

Current production AI source: Jisilu public pages. Xueqiu is now a collect-only Shadow Source: public anonymous Author Shadow + public Hot Exploration are normalized into the same source-agnostic Increment Ledger shape, but are not yet sent to Broad / Final.

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

Xueqiu Shadow Source V0:
- Adapter: `runtime/intelligence_radar/xueqiu.py`; watchlist: `runtime/intelligence_radar/xueqiu_watchlist.json` (initially empty; probe accounts are never promoted automatically).
- Uses normal anonymous public `www.xueqiu.com` sessions; it does not attempt to solve or bypass WAF challenges.
- `XUEQIU_AUTHOR_SHADOW` reads watched-user timelines and distinguishes POST / REPLY / REPOST using stable source identity fields.
- `XUEQIU_HOT_EXPLORATION` is a non-keyword discovery lane based on the public hot feed; it is explicitly biased by platform ranking and is NOT full-site coverage.
- Public comments expose stable comment IDs / parent relationships; bounded comment scanning exists but is off by default.
- Timeline/hot rows marked `truncated=true` are hydrated through the public status-detail endpoint before hashing.
- Reply items prefer `commentId` as the durable item identity while retaining the standalone status URL; edits are handled later by the existing content hash.
- Same item found by multiple Xueqiu lanes is deduplicated once while preserving all discovery paths.
- The adapter maps directly to the existing Increment Ledger candidate shape. No Xueqiu-specific persistence schema is introduced.
- V0 is collect-only: no AI and no production Radar DB writes.
- Shadow accumulation is enabled independently of the Radar AI Scheduler: a lightweight JSON observation archive runs at 07:30 / 12:30 / 17:00 / 21:20 Asia/Shanghai.
- The 07:30 run also revisits the previous logical date so the 21:20 → 07:30 overnight gap can be recovered.
- Archive: `runtime_data/intelligence_radar/xueqiu_shadow/YYYY/MM/YYYY-MM-DD.json`. It keeps IDs, author/time/link, content SHA-256, short excerpt, discovery paths and seen/edit counts; it does not mirror full candidate bodies.
- Repeated observations update `seen_count`; edits update the hash/excerpt and retain a small prior-hash trail.
- The Xueqiu Shadow timer is separate from the still-disabled Radar Daily AI Scheduler.

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

Manual run:

    set -a
    source .runtime_env
    set +a
    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD

Historical backfill run:

    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD --run-mode BACKFILL

Collector-only smoke test:

    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD --collect-only

Xueqiu collect-only Shadow smoke test:

    .venv/bin/python -m runtime.intelligence_radar.xueqiu --date YYYY-MM-DD

Xueqiu Shadow accumulation run:

    .venv/bin/python -m runtime.intelligence_radar.xueqiu_shadow_archive

One-time reviewed V2 history seed:

    .venv/bin/python scripts/backfill_radar_v2_history.py --data-root runtime_data

Radar Daily AI Scheduler remains disabled. Only the Xueqiu Shadow collect-only timer is enabled in V0.3.

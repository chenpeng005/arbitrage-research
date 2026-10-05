# Intelligence Radar V0.2

Goal: scan a small set of high-value investment communities, use AI to keep meaningful daily information increments, persist the daily record, and show it through a stable web page.

Current source: Jisilu public pages. The persistence model is source-agnostic so later sources such as Xueqiu, forums, stock boards and individual self-media can feed the same Object → Node → Evidence structure.

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

Persistence principles:
- Object is a stable noun-like identity: a security/target or a stable strategy name.
- Node is the dated increment about that object; one node should normally have one main object.
- Evidence stores platform, evidence author, time, source title, source URL, reply/comment locator and excerpt.
- AI interpretation fields can evolve; source evidence is the harder factual layer.
- Broad-screen intermediate findings are not persisted.
- A successful scan with zero final findings is still persisted.
- Raw web pages, all comments, images and attachments are not mirrored.
- Re-running the same source/date replaces that source/date snapshot.
- Run mode is recorded as LIVE or BACKFILL.
- Extractor/schema versions are recorded for future reinterpretation and migration.


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

One-time reviewed V2 history seed:

    .venv/bin/python scripts/backfill_radar_v2_history.py --data-root runtime_data

No scheduler is enabled in V0.2.

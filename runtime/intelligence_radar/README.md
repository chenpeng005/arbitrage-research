# Intelligence Radar V0

Goal: scan a small set of high-value investment communities, use AI to keep meaningful daily information increments, persist the daily record, and show it through a stable web page.

V0 source: Jisilu public pages.

Runtime chain:
1. Read the latest-activity feed and the new-topic feed.
2. Fetch candidate question detail pages.
3. Keep the original post as context and extract target-day replies as daily increments.
4. Run a deliberately broad AI screen in small batches.
5. Run a second final gate that selects only items worth appearing in the user's daily page.
6. Persist only the final daily record in SQLite.
7. Serve the daily record through /radar and /api/radar/*.

Persistence:
- The durable product is the final daily record.
- Broad-screen intermediate findings are not persisted.
- A successful scan with zero final findings is still persisted.
- Raw web pages, all comments, images and attachments are not mirrored.
- Re-running the same source/date replaces that day's snapshot.

Manual run:

    set -a
    source .runtime_env
    set +a
    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD

Collector-only smoke test:

    .venv/bin/python -m runtime.intelligence_radar.daily --date YYYY-MM-DD --collect-only

No scheduler is enabled in V0.

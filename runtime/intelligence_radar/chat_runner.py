from __future__ import annotations

import json
import os
from pathlib import Path

from runtime.ai_runtime.file_chat_provider import FileChatProvider
import runtime.intelligence_radar.daily as daily
import runtime.intelligence_radar.xueqiu_daily as xueqiu_daily
from runtime.intelligence_radar.run_all import run_all


def _chat_provider():
    return FileChatProvider(scope=os.environ.get("CHAT_PROVIDER_SCOPE", "radar"))


# The radar modules import their provider factory by value, so replace the
# module globals for this execution profile only.
daily.build_provider_from_env = _chat_provider
xueqiu_daily.build_provider_from_env = _chat_provider


def main() -> None:
    data_root = Path(os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"))
    run_date = daily.china_now().date().isoformat()
    result = run_all(
        data_root=data_root,
        run_date=run_date,
        run_mode="LIVE",
        jisilu_batch_size=int(os.environ.get("RADAR_CHAT_JISILU_BATCH_SIZE", "24")),
        xueqiu_batch_size=int(os.environ.get("RADAR_CHAT_XUEQIU_BATCH_SIZE", "60")),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

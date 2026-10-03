"""离线探测：直接抓取全部信源，输出可用性与样例。

用途：验证每个 RSS / Telegram / radar 信源是否真的能抓到内容。
不推送、不写 state、不依赖 Telegram 凭据。

用法（仓库根目录）：
    python -m probe.probe_all
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from pusher.sources import SOURCE_TYPES  # noqa: E402,F401 — 触发注册
from pusher.sources.base import load_sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    cfg = yaml.safe_load((ROOT / "config" / "sources.yml").read_text(encoding="utf-8"))
    sources = load_sources(cfg, log=lambda m: print(m, flush=True))
    print(f"\n=== {len(sources)} sources ===\n", flush=True)
    ok_count = 0
    for source in sources:
        try:
            items = source.fetch(log=lambda *_: None)
        except Exception as exc:  # noqa: BLE001 — 探测就是要看失败
            print(f"FAIL  {source.id:18s} {type(exc).__name__}: {str(exc)[:150]}", flush=True)
            continue
        if items:
            ok_count += 1
            print(f"OK    {source.id:18s} {len(items):3d} items", flush=True)
            for item in items[:3]:
                print(f"        · {item.title[:70]}", flush=True)
                print(f"          {item.url[:100]}", flush=True)
        else:
            print(f"EMPTY {source.id:18s} 0 items（抓到了但没有条目）", flush=True)
    print(f"\n=== usable: {ok_count}/{len(sources)} ===\n", flush=True)


if __name__ == "__main__":
    main()

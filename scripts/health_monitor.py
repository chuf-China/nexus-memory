#!/usr/bin/env python3
"""nexus_health_monitor.py — 定时检查 Nexus 四层健康状态。

由 Hermes cron 调度，每 6 小时运行一次。
异常时输出告警信息供 cron 通知。
"""

import json
import sys
from pathlib import Path

# Add hermes-agent to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hermes-agent"))

from ..health import health_check, format_health

DB_PATH = str(Path.home() / ".hermes" / "data" / "nexus.db")


def main():
    result = health_check(DB_PATH)
    print(format_health(result))

    # Exit non-zero if any layer is degraded
    overall = result.get("overall", "error")
    if overall == "error":
        print("\n[ALERT] Nexus 健康检查发现错误，需要关注！")
        sys.exit(2)
    elif overall == "warn":
        print("\n[WARN] Nexus 部分组件降级，建议排查。")
        sys.exit(1)
    else:
        print("\n[OK] Nexus 四层全部正常。")
        sys.exit(0)


if __name__ == "__main__":
    main()

"""版本追溯器 — 记录流水线全过程的操作日志和成本"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class VersionLogEntry:
    """单条操作日志"""
    timestamp: str
    stage: str
    action: str
    cost_usd: float = 0.0
    metadata: dict = field(default_factory=dict)


class VersionTracker:
    """轻量级操作日志 + 成本追踪"""

    def __init__(self):
        self._log: list[VersionLogEntry] = []
        self._start_time = time.time()

    def log(self, stage: str, action: str, cost_usd: float = 0.0, **metadata):
        self._log.append(VersionLogEntry(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            stage=stage,
            action=action,
            cost_usd=cost_usd,
            metadata=metadata,
        ))

    def get_log(self) -> list[dict]:
        return [
            {
                "time": e.timestamp,
                "stage": e.stage,
                "action": e.action,
                "cost": f"${e.cost_usd:.4f}",
                **e.metadata,
            }
            for e in self._log
        ]

    @property
    def total_cost(self) -> float:
        return sum(e.cost_usd for e in self._log)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    def summary(self) -> str:
        lines = [
            f"版本追溯报告",
            f"总操作数: {len(self._log)}",
            f"总成本: ${self.total_cost:.4f}",
            f"总耗时: {self.elapsed_seconds:.1f}秒",
            "",
        ]
        for e in self._log:
            lines.append(f"  [{e.stage}] {e.action} - ${e.cost_usd:.4f}")
        return "\n".join(lines)

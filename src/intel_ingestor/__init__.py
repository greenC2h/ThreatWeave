"""intel_ingestor：确定性情报采集与初步格式化。

对外暴露的核心入口是 :func:`intel_ingestor.ingestor.collect_source`，供 A 的采集工具
调用；深度清洗和 Java MCP 入库由 Agent 执行。
"""

from __future__ import annotations

from intel_ingestor.ingestor import collect_source
from intel_ingestor.schema import CollectionReport, SourceConfig

__all__ = ["collect_source", "CollectionReport", "SourceConfig"]

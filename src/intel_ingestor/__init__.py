"""ThreatPipeline 使用的确定性情报采集组件。

对外暴露的核心入口是 :func:`intel_ingestor.ingestor.collect_source`。深度清洗、写入和
抽取均由 ThreatPipeline 执行。
"""

from __future__ import annotations

from intel_ingestor.ingestor import collect_source
from intel_ingestor.schema import CollectionReport, SourceConfig

__all__ = ["collect_source", "CollectionReport", "SourceConfig"]

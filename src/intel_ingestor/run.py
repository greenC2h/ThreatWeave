r"""命令行入口：手动触发一次来源采集，用于端到端真实验证。

示例：
    .\.venv\Scripts\python.exe -m intel_ingestor.run --source cncert_cc_threat_warning --max-articles 5

需要 ``PYTHONPATH=src``。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from intel_ingestor import collect_source

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="触发一次 ThreatWeave 来源采集")
    parser.add_argument("--source", default="cncert_cc_threat_warning", help="来源 ID（对应 sources/<id>.yaml）")
    parser.add_argument("--max-articles", type=int, default=None, help="限制本次采集的文章数量")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(levelname)s %(name)s: %(message)s")
    report = asyncio.run(collect_source(args.source, max_articles=args.max_articles))
    print(report.to_agent_payload())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

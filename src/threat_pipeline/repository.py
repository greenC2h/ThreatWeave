"""保存 ThreatPipeline 的最小运行状态，不保存模型中间文本。"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from agent.config import POSTGRES_URI
from threat_pipeline.schema import ProcessingRecord


ConnectionFactory = Callable[[], Awaitable[AsyncConnection[Any]]]
RUNNING_LEASE_SECONDS = max(60, int(os.getenv("THREATWEAVE_PIPELINE_RUNNING_LEASE_SECONDS", "7200")))


class PipelineRepository:
    """以短连接维护导入幂等、运行租约和失败状态。"""

    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._connection_factory = connection_factory or self._connect

    @staticmethod
    async def _connect() -> AsyncConnection[Any]:
        return await AsyncConnection.connect(
            POSTGRES_URI, autocommit=True, prepare_threshold=0, row_factory=dict_row,
        )

    async def ensure_schema(self) -> None:
        """创建保留的处理状态表，并清理已废弃的草稿和授权表。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("CREATE SCHEMA IF NOT EXISTS workflow")
                await cursor.execute("DROP TABLE IF EXISTS workflow.extraction_drafts")
                await cursor.execute("DROP TABLE IF EXISTS workflow.draft_access_grants")
                await cursor.execute("DROP TABLE IF EXISTS workflow.formatting_access_grants")
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS workflow.document_processing (
                        doc_key TEXT PRIMARY KEY,
                        source_id TEXT,
                        external_id TEXT,
                        url TEXT,
                        document_id BIGINT,
                        source_fingerprint TEXT,
                        formatted_content_sha256 TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        current_run_id TEXT,
                        last_failed_stage TEXT,
                        last_error TEXT,
                        formatted_at TIMESTAMPTZ,
                        extracted_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                await cursor.execute("ALTER TABLE workflow.document_processing ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'")
                await cursor.execute("ALTER TABLE workflow.document_processing ADD COLUMN IF NOT EXISTS current_run_id TEXT")
                await cursor.execute("ALTER TABLE workflow.document_processing ADD COLUMN IF NOT EXISTS last_failed_stage TEXT")
                await cursor.execute("ALTER TABLE workflow.document_processing ADD COLUMN IF NOT EXISTS last_error TEXT")
                # 旧工作流的双阶段状态已经没有调用方，避免它们继续成为错误的运维信号。
                await cursor.execute("ALTER TABLE workflow.document_processing DROP COLUMN IF EXISTS formatting_status")
                await cursor.execute("ALTER TABLE workflow.document_processing DROP COLUMN IF EXISTS extraction_status")
                await cursor.execute("ALTER TABLE workflow.document_processing DROP COLUMN IF EXISTS extracted_content_sha256")
                await cursor.execute("ALTER TABLE workflow.document_processing DROP COLUMN IF EXISTS current_workflow_id")

    async def begin(
        self,
        *,
        doc_key: str,
        source_id: str,
        external_id: str | None,
        url: str,
        source_fingerprint: str,
        run_id: str,
        force_refresh: bool,
    ) -> tuple[bool, ProcessingRecord]:
        """取得文章处理租约；正文来源未变且已完成时跳过。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO workflow.document_processing (
                        doc_key, source_id, external_id, url, source_fingerprint, status, current_run_id, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'running', %s, now())
                    ON CONFLICT (doc_key) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        external_id = EXCLUDED.external_id,
                        url = EXCLUDED.url,
                        source_fingerprint = EXCLUDED.source_fingerprint,
                        status = CASE
                            WHEN workflow.document_processing.status = 'running'
                                 AND workflow.document_processing.updated_at > now() - (%s * interval '1 second')
                                THEN 'running'
                            WHEN %s OR workflow.document_processing.source_fingerprint IS DISTINCT FROM EXCLUDED.source_fingerprint
                                 OR workflow.document_processing.status <> 'completed'
                                THEN 'running'
                            ELSE workflow.document_processing.status
                        END,
                        current_run_id = CASE
                            WHEN workflow.document_processing.status = 'running'
                                 AND workflow.document_processing.updated_at > now() - (%s * interval '1 second')
                                THEN workflow.document_processing.current_run_id
                            WHEN %s OR workflow.document_processing.source_fingerprint IS DISTINCT FROM EXCLUDED.source_fingerprint
                                 OR workflow.document_processing.status <> 'completed'
                                THEN EXCLUDED.current_run_id
                            ELSE workflow.document_processing.current_run_id
                        END,
                        last_failed_stage = CASE WHEN %s THEN NULL ELSE workflow.document_processing.last_failed_stage END,
                        last_error = CASE WHEN %s THEN NULL ELSE workflow.document_processing.last_error END,
                        updated_at = now()
                    RETURNING *, status = 'running' AND current_run_id = %s AS acquired
                """, (
                    doc_key, source_id, external_id, url, source_fingerprint, run_id,
                    RUNNING_LEASE_SECONDS, force_refresh, RUNNING_LEASE_SECONDS, force_refresh,
                    force_refresh, force_refresh, run_id,
                ))
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("无法创建文章处理状态")
        acquired = bool(row.pop("acquired"))
        return acquired, ProcessingRecord.model_validate(row)

    async def record_formatted(
        self, *, doc_key: str, document_id: int, content_sha256: str, run_id: str,
    ) -> None:
        """记录 Java 已确认的规范正文。"""
        await self._update_owned(doc_key, run_id, """
            document_id = %s, formatted_content_sha256 = %s, formatted_at = now(), updated_at = now()
        """, (document_id, content_sha256))

    async def mark_completed(self, doc_key: str, run_id: str) -> None:
        """标记全文抽取和写入均已完成。"""
        await self._update_owned(doc_key, run_id, "status = 'completed', extracted_at = now(), updated_at = now()", ())

    async def mark_failed(self, doc_key: str, run_id: str, stage: str, error: str) -> None:
        """记录失败阶段，供下一次请求从头重新处理。"""
        await self._update_owned(doc_key, run_id, """
            status = 'failed', last_failed_stage = %s, last_error = %s, updated_at = now()
        """, (stage, error[:1000]))

    async def _update_owned(self, doc_key: str, run_id: str, assignments: str, params: tuple[Any, ...]) -> None:
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE workflow.document_processing SET {assignments} WHERE doc_key = %s AND current_run_id = %s",
                    (*params, doc_key, run_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("文章处理租约已失效")

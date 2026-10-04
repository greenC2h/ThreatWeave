"""持久化工作流运行状态，不承载 ThreatWeave 业务文档或图谱数据。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from agent.config import POSTGRES_URI
from intelligence_workflow.schema import (
    DraftAccessGrant,
    ExtractionDraft,
    ExtractionStatus,
    FormattingAccessGrant,
    FormattingStatus,
    ProcessingRecord,
)


ConnectionFactory = Callable[[], Awaitable[AsyncConnection[Any]]]


def _running_lease_seconds() -> int:
    """读取可恢复的运行租约，避免进程崩溃留下永久的 running 状态。"""
    value = int(os.getenv("THREATWEAVE_WORKFLOW_RUNNING_LEASE_SECONDS", "7200"))
    if value < 60:
        raise ValueError("THREATWEAVE_WORKFLOW_RUNNING_LEASE_SECONDS 必须不小于 60")
    return value


RUNNING_LEASE_SECONDS = _running_lease_seconds()


class WorkflowRepository:
    """以短连接访问 `workflow` schema，隔离运行状态与业务 CRUD。"""

    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._connection_factory = connection_factory or self._connect

    @staticmethod
    async def _connect() -> AsyncConnection[Any]:
        """创建不会与 Agent 生命周期绑定的工作流数据库连接。"""
        return await AsyncConnection.connect(
            POSTGRES_URI,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )

    async def ensure_schema(self) -> None:
        """幂等创建运行控制表；不修改 Java 拥有的 `threatweave` schema。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("CREATE SCHEMA IF NOT EXISTS workflow")
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS workflow.document_processing (
                        doc_key TEXT PRIMARY KEY,
                        source_id TEXT,
                        external_id TEXT,
                        url TEXT,
                        document_id BIGINT,
                        source_fingerprint TEXT,
                        formatted_content_sha256 TEXT,
                        formatting_status TEXT NOT NULL DEFAULT 'pending',
                        extraction_status TEXT NOT NULL DEFAULT 'not_requested',
                        extracted_content_sha256 TEXT,
                        current_workflow_id TEXT,
                        last_failed_stage TEXT,
                        last_error TEXT,
                        formatted_at TIMESTAMPTZ,
                        extracted_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CHECK (formatting_status IN ('pending', 'running', 'completed', 'failed')),
                        CHECK (extraction_status IN ('not_requested', 'pending', 'running', 'completed', 'stale', 'failed'))
                    )
                """)
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS workflow.extraction_drafts (
                        draft_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        document_id BIGINT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        payload_json JSONB NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                """)
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS workflow.draft_access_grants (
                        token TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        document_id BIGINT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        purpose TEXT NOT NULL CHECK (purpose IN ('preview', 'commit')),
                        draft_id TEXT,
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                """)
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS workflow.formatting_access_grants (
                        token TEXT PRIMARY KEY,
                        doc_key TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        external_id TEXT,
                        url TEXT,
                        published_at TEXT,
                        expires_at TIMESTAMPTZ NOT NULL
                    )
                """)
                await cursor.execute("""
                    CREATE INDEX IF NOT EXISTS document_processing_source_status_idx
                    ON workflow.document_processing (source_id, formatting_status, extraction_status)
                """)
                await cursor.execute("""
                    CREATE INDEX IF NOT EXISTS extraction_drafts_lookup_idx
                    ON workflow.extraction_drafts (user_id, document_id, content_sha256, expires_at DESC)
                """)

    async def get_processing(self, doc_key: str) -> ProcessingRecord | None:
        """按稳定文档键读取当前处理状态。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT * FROM workflow.document_processing WHERE doc_key = %s",
                    (doc_key,),
                )
                row = await cursor.fetchone()
        return ProcessingRecord.model_validate(row) if row else None

    async def begin_formatting(
        self,
        *,
        doc_key: str,
        source_id: str,
        external_id: str | None,
        url: str,
        source_fingerprint: str,
        workflow_id: str,
        force_refresh: bool,
    ) -> tuple[bool, ProcessingRecord]:
        """占用 A 阶段；同一采集草稿已完成时返回跳过而不重复调用模型。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO workflow.document_processing (
                        doc_key, source_id, external_id, url, source_fingerprint,
                        formatting_status, extraction_status, current_workflow_id, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'running', 'not_requested', %s, now())
                    ON CONFLICT (doc_key) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        external_id = EXCLUDED.external_id,
                        url = EXCLUDED.url,
                        source_fingerprint = EXCLUDED.source_fingerprint,
                        formatting_status = CASE
                            WHEN workflow.document_processing.formatting_status = 'running'
                                AND workflow.document_processing.updated_at >= now() - make_interval(secs => %s)
                                THEN workflow.document_processing.formatting_status
                            WHEN %s THEN 'running'
                            WHEN workflow.document_processing.source_fingerprint IS DISTINCT FROM EXCLUDED.source_fingerprint THEN 'running'
                            WHEN workflow.document_processing.formatting_status IN ('failed', 'pending') THEN 'running'
                            ELSE workflow.document_processing.formatting_status
                        END,
                        current_workflow_id = CASE
                            WHEN workflow.document_processing.formatting_status = 'running'
                                AND workflow.document_processing.updated_at >= now() - make_interval(secs => %s)
                                THEN workflow.document_processing.current_workflow_id
                            WHEN %s OR workflow.document_processing.source_fingerprint IS DISTINCT FROM EXCLUDED.source_fingerprint
                                OR workflow.document_processing.formatting_status IN ('failed', 'pending')
                            THEN EXCLUDED.current_workflow_id
                            ELSE workflow.document_processing.current_workflow_id
                        END,
                        updated_at = now()
                    RETURNING *, formatting_status = 'running' AND current_workflow_id = %s AS acquired
                """, (
                    doc_key, source_id, external_id, url, source_fingerprint, workflow_id,
                    RUNNING_LEASE_SECONDS, force_refresh, RUNNING_LEASE_SECONDS, force_refresh,
                    workflow_id,
                ))
                row = await cursor.fetchone()
        if not row:
            raise RuntimeError("无法创建工作流格式化状态")
        acquired = bool(row.pop("acquired"))
        return acquired, ProcessingRecord.model_validate(row)

    async def record_formatted_document(
        self,
        *,
        doc_key: str,
        source_id: str,
        external_id: str | None,
        url: str | None,
        document_id: int,
        content_sha256: str,
        workflow_id: str,
        source_fingerprint: str | None = None,
    ) -> ProcessingRecord:
        """记录 A 的成功结果，并在正文变化时使既有抽取显式过期。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO workflow.document_processing (
                        doc_key, source_id, external_id, url, document_id,
                        source_fingerprint, formatted_content_sha256, formatting_status, extraction_status,
                        current_workflow_id, formatted_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed', 'not_requested', %s, now(), now())
                    ON CONFLICT (doc_key) DO UPDATE SET
                        source_id = EXCLUDED.source_id,
                        external_id = EXCLUDED.external_id,
                        url = EXCLUDED.url,
                        document_id = EXCLUDED.document_id,
                        source_fingerprint = COALESCE(EXCLUDED.source_fingerprint, workflow.document_processing.source_fingerprint),
                        extraction_status = CASE
                            WHEN workflow.document_processing.formatted_content_sha256 IS DISTINCT FROM EXCLUDED.formatted_content_sha256
                            THEN 'stale'
                            ELSE workflow.document_processing.extraction_status
                        END,
                        formatted_content_sha256 = EXCLUDED.formatted_content_sha256,
                        formatting_status = 'completed',
                        current_workflow_id = EXCLUDED.current_workflow_id,
                        last_failed_stage = NULL,
                        last_error = NULL,
                        formatted_at = now(),
                        updated_at = now()
                    RETURNING *
                """, (doc_key, source_id, external_id, url, document_id, source_fingerprint, content_sha256, workflow_id))
                row = await cursor.fetchone()
        return ProcessingRecord.model_validate(row)

    async def begin_extraction(
        self,
        *,
        doc_key: str,
        workflow_id: str,
        force_refresh: bool,
    ) -> tuple[bool, ProcessingRecord]:
        """原子占用 B 阶段，避免两个工作流同时对同一正文写入图谱。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    UPDATE workflow.document_processing
                    SET extraction_status = CASE
                            WHEN extraction_status = 'running'
                                AND updated_at >= now() - make_interval(secs => %s)
                                THEN extraction_status
                            WHEN %s THEN 'running'
                            WHEN extraction_status IN ('not_requested', 'pending', 'stale', 'failed') THEN 'running'
                            ELSE extraction_status
                        END,
                        current_workflow_id = CASE
                            WHEN extraction_status = 'running'
                                AND updated_at >= now() - make_interval(secs => %s)
                                THEN current_workflow_id
                            WHEN %s OR extraction_status IN ('not_requested', 'pending', 'stale', 'failed')
                                THEN %s
                            ELSE current_workflow_id
                        END,
                        last_failed_stage = CASE
                            WHEN extraction_status = 'running'
                                AND updated_at >= now() - make_interval(secs => %s)
                                THEN last_failed_stage
                            ELSE NULL
                        END,
                        last_error = CASE
                            WHEN extraction_status = 'running'
                                AND updated_at >= now() - make_interval(secs => %s)
                                THEN last_error
                            ELSE NULL
                        END,
                        updated_at = now()
                    WHERE doc_key = %s
                    RETURNING *, extraction_status = 'running' AND current_workflow_id = %s AS acquired
                """, (
                    RUNNING_LEASE_SECONDS,
                    force_refresh,
                    RUNNING_LEASE_SECONDS,
                    force_refresh,
                    workflow_id,
                    RUNNING_LEASE_SECONDS,
                    RUNNING_LEASE_SECONDS,
                    doc_key,
                    workflow_id,
                ))
                row = await cursor.fetchone()
        if not row:
            raise ValueError(f"未找到工作流文档状态: {doc_key}")
        acquired = bool(row.pop("acquired"))
        return acquired, ProcessingRecord.model_validate(row)

    async def mark_extraction_completed(
        self,
        doc_key: str,
        content_sha256: str,
        workflow_id: str,
    ) -> ProcessingRecord:
        """仅当抽取对应当前正文时将状态置为完成。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    UPDATE workflow.document_processing
                    SET extraction_status = 'completed', extracted_content_sha256 = %s,
                        current_workflow_id = %s, last_failed_stage = NULL, last_error = NULL,
                        extracted_at = now(), updated_at = now()
                    WHERE doc_key = %s AND formatted_content_sha256 = %s
                      AND extraction_status = 'running' AND current_workflow_id = %s
                    RETURNING *
                """, (content_sha256, workflow_id, doc_key, content_sha256, workflow_id))
                row = await cursor.fetchone()
        if not row:
            raise ValueError("当前文档正文已变化，不能将旧抽取标记为完成")
        return ProcessingRecord.model_validate(row)

    async def mark_stage_failed(
        self,
        doc_key: str,
        stage: str,
        error: str,
        workflow_id: str,
    ) -> ProcessingRecord:
        """记录可向用户显示的受限失败摘要，不持久化异常堆栈或敏感内容。"""
        if stage not in {"formatting", "extraction"}:
            raise ValueError("stage 必须是 formatting 或 extraction")
        status_column = f"{stage}_status"
        safe_error = " ".join(error.split())[:500]
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"""
                    UPDATE workflow.document_processing
                    SET {status_column} = 'failed', last_failed_stage = %s,
                        last_error = %s, updated_at = now()
                    WHERE doc_key = %s AND current_workflow_id = %s
                    RETURNING *
                    """,
                    (stage, safe_error, doc_key, workflow_id),
                )
                row = await cursor.fetchone()
        if not row:
            raise ValueError(f"未找到工作流文档状态: {doc_key}")
        return ProcessingRecord.model_validate(row)

    async def list_processing(self, source_id: str | None = None) -> list[ProcessingRecord]:
        """列出指定来源或全部来源的处理状态，供主 Agent 回答待抽取查询。"""
        query = "SELECT * FROM workflow.document_processing"
        params: tuple[str, ...] = ()
        if source_id:
            query += " WHERE source_id = %s"
            params = (source_id,)
        query += " ORDER BY updated_at DESC"
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, params)
                rows = await cursor.fetchall()
        return [ProcessingRecord.model_validate(row) for row in rows]

    async def save_draft(
        self,
        *,
        user_id: str,
        document_id: int,
        content_sha256: str,
        payload: dict[str, Any],
        ttl: timedelta = timedelta(days=7),
    ) -> ExtractionDraft:
        """保存仅抽取结果，默认七天过期且不向图谱写入任何数据。"""
        draft_id = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO workflow.extraction_drafts (
                        draft_id, user_id, document_id, content_sha256, payload_json, expires_at
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    RETURNING *
                """, (draft_id, user_id, document_id, content_sha256, json.dumps(payload), expires_at))
                row = await cursor.fetchone()
        return ExtractionDraft.model_validate({**row, "payload": row.pop("payload_json")})

    async def issue_draft_access_grant(
        self,
        *,
        user_id: str,
        document_id: int,
        content_sha256: str,
        purpose: str,
        draft_id: str | None = None,
        ttl: timedelta = timedelta(minutes=15),
    ) -> DraftAccessGrant:
        """签发一次性内部令牌，避免模型控制用户或草稿标识。"""
        if purpose not in {"preview", "commit"}:
            raise ValueError("purpose 必须是 preview 或 commit")
        if purpose == "commit" and not draft_id:
            raise ValueError("提交草稿必须绑定 draft_id")
        token = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO workflow.draft_access_grants (
                        token, user_id, document_id, content_sha256, purpose, draft_id, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (token, user_id, document_id, content_sha256, purpose, draft_id, expires_at))
        return DraftAccessGrant(
            token=token,
            user_id=user_id,
            document_id=document_id,
            content_sha256=content_sha256,
            purpose=purpose,
            draft_id=draft_id,
        )

    async def consume_draft_access_grant(self, token: str, purpose: str) -> DraftAccessGrant:
        """原子消费未过期授权，令模型一次调用不能跨用户或重复提交草稿。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    DELETE FROM workflow.draft_access_grants
                    WHERE token = %s AND purpose = %s AND expires_at > now()
                    RETURNING token, user_id, document_id, content_sha256, purpose, draft_id
                """, (token, purpose))
                row = await cursor.fetchone()
        if not row:
            raise ValueError("草稿内部访问授权不存在、已过期或已使用")
        return DraftAccessGrant.model_validate(row)

    async def issue_formatting_access_grant(
        self,
        *,
        doc_key: str,
        source_id: str,
        source_name: str,
        external_id: str | None,
        url: str,
        published_at: str | None,
        ttl: timedelta = timedelta(minutes=30),
    ) -> FormattingAccessGrant:
        """绑定一篇待格式化文章的身份，禁止 A 选择其他文档进行写入。"""
        token = str(uuid4())
        expires_at = datetime.now(timezone.utc) + ttl
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    INSERT INTO workflow.formatting_access_grants (
                        token, doc_key, source_id, source_name, external_id, url, published_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (token, doc_key, source_id, source_name, external_id, url, published_at, expires_at))
        return FormattingAccessGrant(
            token=token,
            doc_key=doc_key,
            source_id=source_id,
            source_name=source_name,
            external_id=external_id,
            url=url,
            published_at=published_at,
        )

    async def consume_formatting_access_grant(self, token: str) -> FormattingAccessGrant:
        """原子消费文章写入授权，防止模型用同一授权重复或跨文档写入。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    DELETE FROM workflow.formatting_access_grants
                    WHERE token = %s AND expires_at > now()
                    RETURNING token, doc_key, source_id, source_name, external_id, url, published_at
                """, (token,))
                row = await cursor.fetchone()
        if not row:
            raise ValueError("格式化文档写入授权不存在、已过期或已使用")
        return FormattingAccessGrant.model_validate(row)

    async def find_active_draft(
        self,
        *,
        user_id: str,
        document_id: int,
        content_sha256: str,
    ) -> ExtractionDraft | None:
        """查找未过期的同用户草稿，正文变化后不会返回旧版本。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    SELECT * FROM workflow.extraction_drafts
                    WHERE user_id = %s AND document_id = %s AND content_sha256 = %s
                      AND status = 'active' AND expires_at > now()
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (user_id, document_id, content_sha256))
                row = await cursor.fetchone()
        if not row:
            return None
        return ExtractionDraft.model_validate({**row, "payload": row.pop("payload_json")})

    async def get_active_draft_by_id(self, draft_id: str, user_id: str) -> ExtractionDraft | None:
        """按草稿标识读取当前用户的有效草稿，供受控提交工具二次校验。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("""
                    SELECT * FROM workflow.extraction_drafts
                    WHERE draft_id = %s AND user_id = %s AND status = 'active' AND expires_at > now()
                """, (draft_id, user_id))
                row = await cursor.fetchone()
        if not row:
            return None
        return ExtractionDraft.model_validate({**row, "payload": row.pop("payload_json")})

    async def mark_draft_committed(self, draft_id: str) -> None:
        """将已成功写入图谱的草稿标记为已提交，防止后续重复复用。"""
        async with await self._connection_factory() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE workflow.extraction_drafts SET status = 'committed' WHERE draft_id = %s",
                    (draft_id,),
                )

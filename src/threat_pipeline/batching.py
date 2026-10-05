"""ThreatPipeline 的确定性文章分批策略。"""

from __future__ import annotations

from intel_ingestor.schema import CollectedDocument


MAX_DOCUMENTS_PER_BATCH = 3
MAX_BATCH_INPUT_CHARACTERS = 24_000


def split_document_batches(
    documents: list[CollectedDocument],
    *,
    max_documents: int = MAX_DOCUMENTS_PER_BATCH,
    max_characters: int = MAX_BATCH_INPUT_CHARACTERS,
) -> list[list[CollectedDocument]]:
    """按篇数和输入字符预算顺序分批，不截断任一文章。"""
    if max_documents < 1 or max_characters < 1:
        raise ValueError("批次篇数和字符预算必须大于零")

    batches: list[list[CollectedDocument]] = []
    current: list[CollectedDocument] = []
    current_size = 0
    for document in documents:
        document_size = len(document.preliminary_content) + len(document.title) + len(document.url)
        would_exceed = current and (
            len(current) >= max_documents or current_size + document_size > max_characters
        )
        if would_exceed:
            batches.append(current)
            current = []
            current_size = 0
        current.append(document)
        current_size += document_size
    if current:
        batches.append(current)
    return batches

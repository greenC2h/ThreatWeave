"""将模型候选规范化为带精确正文出处的 Java 抽取请求。"""

from __future__ import annotations

from typing import Any


VALID_ENTITY_TYPES = frozenset({
    "ipv4", "ipv6", "domain", "url", "file_hash", "cve", "threat_actor",
    "malware", "campaign", "attack_technique", "tool", "organization",
})
VALID_RELATION_TYPES = frozenset({
    "USES", "ATTRIBUTED_TO", "INDICATES", "RESOLVES_TO", "TARGETS", "EXPLOITS", "COMMUNICATES_WITH",
})
VALID_SEMANTIC_ROLES = frozenset({"malicious_infrastructure", "victim", "research", "unknown"})
MAX_DOCUMENT_CHUNK_CHARACTERS = 8_000


def split_document_content(content: str, max_characters: int = MAX_DOCUMENT_CHUNK_CHARACTERS) -> list[str]:
    """按段落优先切分正文，不遗漏字符且限制单次模型输入。"""
    if max_characters < 1:
        raise ValueError("max_characters 必须大于零")
    chunks: list[str] = []
    current = ""
    for paragraph in content.splitlines(keepends=True):
        if current and len(current) + len(paragraph) > max_characters:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_characters:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:max_characters])
            paragraph = paragraph[max_characters:]
        current += paragraph
    if current or not chunks:
        chunks.append(current)
    return chunks


def build_extraction_payload(content: str, raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """校验字段和值域，并把唯一 evidence 转成 Java 所需的字符位置。"""
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    rejected: list[str] = []
    entity_keys: set[tuple[str, str]] = set()
    relation_keys: set[tuple[str, str, str, str, str]] = set()

    for index, candidate in enumerate(_records(raw.get("entities"))):
        try:
            entity_type = _text(candidate, "entityType", "entity_type")
            canonical_value = _text(candidate, "canonicalValue", "canonical_value")
            role = str(candidate.get("semanticRole", candidate.get("semantic_role", "unknown"))).strip() or "unknown"
            if entity_type not in VALID_ENTITY_TYPES or role not in VALID_SEMANTIC_ROLES:
                raise ValueError("实体类型或语义角色不在允许值域")
            evidence = _evidence(content, candidate.get("evidence"), candidate.get("confidence"))
            key = (entity_type, canonical_value)
            if key in entity_keys:
                continue
            entity_keys.add(key)
            entities.append({
                "entityType": entity_type,
                "canonicalValue": canonical_value,
                "displayName": _optional_text(candidate, "displayName", "display_name"),
                "semanticRole": role,
                "confidence": _confidence(candidate.get("confidence")),
                "aliases": _string_list(candidate.get("aliases")),
                "evidence": [evidence],
            })
        except ValueError as exc:
            rejected.append(f"entities[{index}]：{exc}")

    for index, candidate in enumerate(_records(raw.get("relations"))):
        try:
            source_type = _text(candidate, "srcEntityType", "src_entity_type")
            source_value = _text(candidate, "srcCanonicalValue", "src_canonical_value")
            target_type = _text(candidate, "dstEntityType", "dst_entity_type")
            target_value = _text(candidate, "dstCanonicalValue", "dst_canonical_value")
            relation_type = _text(candidate, "relationType", "relation_type")
            if source_type not in VALID_ENTITY_TYPES or target_type not in VALID_ENTITY_TYPES:
                raise ValueError("关系端点实体类型不在允许值域")
            if relation_type not in VALID_RELATION_TYPES:
                raise ValueError("关系类型不在允许值域")
            if (source_type, source_value) not in entity_keys or (target_type, target_value) not in entity_keys:
                raise ValueError("关系端点必须由同次抽取的实体支持")
            evidence = _evidence(content, candidate.get("evidence"), candidate.get("confidence"))
            key = (source_type, source_value, target_type, target_value, relation_type)
            if key in relation_keys:
                continue
            relation_keys.add(key)
            relations.append({
                "srcEntityType": source_type,
                "srcCanonicalValue": source_value,
                "dstEntityType": target_type,
                "dstCanonicalValue": target_value,
                "relationType": relation_type,
                "confidence": _confidence(candidate.get("confidence")),
                "evidence": [evidence],
            })
        except ValueError as exc:
            rejected.append(f"relations[{index}]：{exc}")
    return entities, relations, rejected


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("候选列表必须是对象数组")
    return value


def _text(value: dict[str, Any], *names: str) -> str:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError(f"缺少字段 {names[0]}")


def _optional_text(value: dict[str, Any], *names: str) -> str | None:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _evidence(content: str, value: Any, confidence: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("evidence 必须是正文中唯一出现的非空引文")
    quote = value.strip()
    start = content.find(quote)
    if start < 0 or content.find(quote, start + 1) >= 0:
        raise ValueError("evidence 必须在正文中唯一出现")
    return {
        "evidenceQuote": quote,
        "charStart": start,
        "charEnd": start + len(quote),
        "extractor": "threat_pipeline",
        "confidence": _confidence(confidence),
    }


def _confidence(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("confidence 必须是 0 到 100 的整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence 必须是 0 到 100 的整数") from exc
    if not 0 <= result <= 100:
        raise ValueError("confidence 必须是 0 到 100 的整数")
    return result


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("aliases 必须是字符串数组")
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))

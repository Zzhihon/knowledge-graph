"""Qdrant vector store wrapper for knowledge graph semantic search.

Provides a thin abstraction over qdrant_client for upserting,
searching, and managing knowledge entry embeddings with local
disk persistence.  Supports filtered search by domain, type,
depth, status, and other metadata fields.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models

from agents.config import ProjectConfig, load_config

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "knowledge_entries"
_BATCH_SIZE = 100


def _get_vector_dim() -> int:
    """Get vector dimension from config (supports both local and Gemini embeddings)."""
    try:
        config = load_config()
        return config.agent.embedding_dim
    except Exception:
        return 384  # fallback to local model dimension


_VECTOR_DIM = _get_vector_dim()


def _entry_id_to_point_id(entry_id: str) -> int:
    """将 entry_id 转换为 Qdrant 所需的 int64 类型 point ID.

    使用 SHA-256 哈希的前 15 个十六进制字符（60 bit）生成稳定的
    整数标识符，确保同一 entry_id 始终映射到同一 point ID.

    Args:
        entry_id: 知识条目 ID，如 'ke-20260226-goroutine-scheduling'.

    Returns:
        适用于 Qdrant point ID 的正整数.
    """
    return int(hashlib.sha256(entry_id.encode()).hexdigest()[:15], 16)


class VectorStore:
    """Qdrant 向量数据库封装，提供知识条目的向量存储与检索能力.

    支持本地磁盘持久化，基于余弦相似度的向量搜索，以及按
    domain / type / depth / status 等元数据字段的过滤查询.

    典型用法::

        with VectorStore("/path/to/qdrant_db") as store:
            store.init_collection()
            store.upsert_entries(entries, embeddings)
            results = store.search(query_vec, top_k=5)
    """

    def __init__(self, db_path: str) -> None:
        """初始化本地 Qdrant 客户端.

        Args:
            db_path: Qdrant 数据库在磁盘上的存储路径.
        """
        self._db_path = db_path
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self._client: QdrantClient = QdrantClient(path=db_path)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭 Qdrant 客户端连接，释放底层资源."""
        if self._client is not None:
            self._client.close()
            self._client = None  # type: ignore[assignment]

    def __enter__(self) -> VectorStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def init_collection(self) -> None:
        """创建或重建向量集合.

        若同名集合已存在则先删除，随后以 384 维余弦距离重新创建.
        适用于全量重建索引的场景.
        """
        # 若集合已存在则先删除，保证干净重建
        if self._client.collection_exists(_COLLECTION_NAME):
            self._client.delete_collection(_COLLECTION_NAME)
            logger.info("已删除旧集合: %s", _COLLECTION_NAME)

        self._client.create_collection(
            collection_name=_COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=_VECTOR_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info(
            "已创建集合: %s (维度=%d, 距离=Cosine)",
            _COLLECTION_NAME,
            _VECTOR_DIM,
        )

    def ensure_collection(self) -> bool:
        """非破坏性创建集合：已存在则跳过.

        Returns:
            True 表示集合已存在（增量模式可用），False 表示新创建.
        """
        if self._client.collection_exists(_COLLECTION_NAME):
            logger.info("集合已存在: %s", _COLLECTION_NAME)
            return True

        self._client.create_collection(
            collection_name=_COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=_VECTOR_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info("已创建新集合: %s", _COLLECTION_NAME)
        return False

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def upsert_entries(
        self,
        entries: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> int:
        """批量写入知识条目及其向量到 Qdrant.

        每个条目包含 ``metadata`` 字典（含 id / title / domain / tags
        等字段）和 ``content`` 正文.  向量与条目按下标一一对应.

        Args:
            entries: 条目列表，每项包含 ``metadata`` (dict) 和
                     ``content`` (str).
            embeddings: 与 entries 等长的嵌入向量列表，每条为
                        384 维浮点数列表.

        Returns:
            成功写入的 point 数量.

        Raises:
            ValueError: entries 与 embeddings 长度不匹配时抛出.
        """
        if len(entries) != len(embeddings):
            raise ValueError(
                f"条目数量 ({len(entries)}) 与向量数量 ({len(embeddings)}) 不匹配"
            )

        if not entries:
            return 0

        points: list[models.PointStruct] = []
        for entry, vector in zip(entries, embeddings):
            meta = entry.get("metadata", {})
            entry_id = str(meta.get("id", ""))
            if not entry_id:
                logger.warning("跳过缺少 id 的条目: %s", meta.get("title", "?"))
                continue

            point_id = _entry_id_to_point_id(entry_id)

            # 将 domain 规整为列表
            raw_domain = meta.get("domain", [])
            if isinstance(raw_domain, str):
                domain_list = [d.strip() for d in raw_domain.split(",") if d.strip()]
            elif isinstance(raw_domain, list):
                domain_list = [str(d) for d in raw_domain]
            else:
                domain_list = [str(raw_domain)] if raw_domain else []

            # 将 tags 规整为列表
            raw_tags = meta.get("tags", [])
            if isinstance(raw_tags, str):
                tags_list = [t.strip() for t in raw_tags.split(",") if t.strip()]
            elif isinstance(raw_tags, list):
                tags_list = [str(t) for t in raw_tags]
            else:
                tags_list = [str(raw_tags)] if raw_tags else []

            payload: dict[str, Any] = {
                "entry_id": entry_id,
                "title": str(meta.get("title", "")),
                "domain": domain_list,
                "tags": tags_list,
                "type": str(meta.get("type", "")),
                "depth": str(meta.get("depth", "")),
                "status": str(meta.get("status", "")),
                "confidence": float(meta.get("confidence", 0.0)),
                "file_path": str(meta.get("file_path", "")),
                "content_hash": str(meta.get("content_hash", "")),
            }

            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            )

        # 分批 upsert，避免单次请求过大
        upserted = 0
        for i in range(0, len(points), _BATCH_SIZE):
            batch = points[i : i + _BATCH_SIZE]
            self._client.upsert(
                collection_name=_COLLECTION_NAME,
                points=batch,
            )
            upserted += len(batch)
            logger.debug("已写入第 %d-%d 条 (共 %d)", i + 1, i + len(batch), len(points))

        logger.info("upsert 完成: %d 个 point 已写入", upserted)
        return upserted

    # ------------------------------------------------------------------
    # Incremental sync helpers
    # ------------------------------------------------------------------

    def get_all_payloads(self) -> dict[str, dict[str, Any]]:
        """Scroll 所有 point 的 payload（不加载 vector）.

        Returns:
            {entry_id: payload_dict} 映射.
        """
        result: dict[str, dict[str, Any]] = {}
        offset = None

        while True:
            scroll_kwargs: dict[str, Any] = {
                "collection_name": _COLLECTION_NAME,
                "limit": _BATCH_SIZE,
                "with_payload": True,
                "with_vectors": False,
            }
            if offset is not None:
                scroll_kwargs["offset"] = offset

            points, next_offset = self._client.scroll(**scroll_kwargs)

            for point in points:
                payload = point.payload or {}
                eid = payload.get("entry_id", "")
                if eid:
                    result[eid] = payload

            if next_offset is None:
                break
            offset = next_offset

        return result

    def delete_points(self, entry_ids: list[str]) -> int:
        """按 entry_id 批量删除 point.

        Args:
            entry_ids: 要删除的条目 ID 列表.

        Returns:
            删除的 point 数量.
        """
        if not entry_ids:
            return 0

        point_ids = [_entry_id_to_point_id(eid) for eid in entry_ids]
        self._client.delete(
            collection_name=_COLLECTION_NAME,
            points_selector=models.PointIdsList(points=point_ids),
        )
        logger.info("已删除 %d 个 point", len(point_ids))
        return len(point_ids)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """基于向量相似度搜索知识条目，支持元数据过滤.

        Args:
            query_embedding: 查询文本的嵌入向量 (384 维).
            top_k: 返回的最大结果数.
            filters: 可选的元数据过滤条件，键为字段名、值为
                     目标值.  例如 ``{"domain": "golang", "type":
                     "principle"}``.  domain 字段使用 MatchAny
                     语义（因 domain 在 payload 中为列表）.

        Returns:
            结果列表，每项包含 entry_id / title / score / metadata.
        """
        query_filter = self._build_filter(filters) if filters else None

        hits = self._client.query_points(
            collection_name=_COLLECTION_NAME,
            query=query_embedding,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        ).points

        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append({
                "entry_id": payload.get("entry_id", ""),
                "title": payload.get("title", ""),
                "score": hit.score,
                "metadata": payload,
            })

        return results

    def search_similar_to(
        self,
        entry_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """查找与指定条目相似的其他知识条目.

        先通过 entry_id 检索该条目的向量，再执行相似搜索并排除
        自身.

        Args:
            entry_id: 目标条目的 ID，如 'ke-20260226-goroutine-scheduling'.
            top_k: 返回的最大结果数.

        Returns:
            结果列表，每项包含 entry_id / title / score.  若目标
            条目不存在则返回空列表.
        """
        point_id = _entry_id_to_point_id(entry_id)

        # 读取该 point 的向量
        retrieved = self._client.retrieve(
            collection_name=_COLLECTION_NAME,
            ids=[point_id],
            with_vectors=True,
        )

        if not retrieved:
            logger.warning("未找到条目: %s (point_id=%d)", entry_id, point_id)
            return []

        source_vector = retrieved[0].vector
        if source_vector is None:
            logger.warning("条目 %s 缺少向量数据", entry_id)
            return []

        # 搜索 top_k + 1 条结果，排除自身后保留 top_k 条
        hits = self._client.query_points(
            collection_name=_COLLECTION_NAME,
            query=source_vector,
            limit=top_k + 1,
            with_payload=True,
        ).points

        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            hit_entry_id = payload.get("entry_id", "")
            if hit_entry_id == entry_id:
                continue
            results.append({
                "entry_id": hit_entry_id,
                "title": payload.get("title", ""),
                "score": hit.score,
            })
            if len(results) >= top_k:
                break

        return results

    # ------------------------------------------------------------------
    # Cross-domain search
    # ------------------------------------------------------------------

    def search_cross_domain(
        self,
        query_embedding: list[float],
        exclude_domains: list[str],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar entries excluding specified domains.

        Uses Qdrant ``must_not`` filter to exclude entries whose domain
        list overlaps with ``exclude_domains``.

        Args:
            query_embedding: The query vector (384 dims).
            exclude_domains: Domain keys to exclude from results.
            top_k: Maximum number of results.

        Returns:
            List of result dicts with entry_id, title, score, metadata.
        """
        must_not_conditions = [
            models.FieldCondition(
                key="domain",
                match=models.MatchAny(any=exclude_domains),
            )
        ]

        query_filter = models.Filter(must_not=must_not_conditions)

        hits = self._client.query_points(
            collection_name=_COLLECTION_NAME,
            query=query_embedding,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        ).points

        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append({
                "entry_id": payload.get("entry_id", ""),
                "title": payload.get("title", ""),
                "score": hit.score,
                "metadata": payload,
            })

        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """返回集合统计信息.

        Returns:
            包含 points_count / vectors_count / status 等键的字典.
            若集合不存在则返回空统计.
        """
        if not self._client.collection_exists(_COLLECTION_NAME):
            return {
                "collection": _COLLECTION_NAME,
                "exists": False,
                "points_count": 0,
            }

        info = self._client.get_collection(_COLLECTION_NAME)
        return {
            "collection": _COLLECTION_NAME,
            "exists": True,
            "points_count": info.points_count,
            "status": str(info.status),
            "vector_dim": _VECTOR_DIM,
            "distance": "Cosine",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter(
        filters: dict[str, str],
    ) -> models.Filter:
        """将 {field: value} 字典转换为 Qdrant Filter 对象.

        domain 字段使用 MatchAny（因为 payload 中 domain 为列表），
        其余字段使用 MatchValue 精确匹配.

        Args:
            filters: 字段名到目标值的映射.

        Returns:
            构建好的 Qdrant Filter 实例.
        """
        conditions: list[models.FieldCondition] = []

        for field_name, value in filters.items():
            if not value:
                continue

            if field_name == "domain":
                # domain 在 payload 中是列表，使用 MatchAny
                conditions.append(
                    models.FieldCondition(
                        key="domain",
                        match=models.MatchAny(any=[value]),
                    )
                )
            elif field_name == "tags":
                # tags 同样是列表字段
                conditions.append(
                    models.FieldCondition(
                        key="tags",
                        match=models.MatchAny(any=[value]),
                    )
                )
            else:
                conditions.append(
                    models.FieldCondition(
                        key=field_name,
                        match=models.MatchValue(value=value),
                    )
                )

        return models.Filter(must=conditions)


# ======================================================================
# Module-level factory
# ======================================================================


def get_vector_store(config: ProjectConfig | None = None) -> VectorStore:
    """工厂函数: 根据项目配置创建 VectorStore 实例.

    从 config.yaml 的 ``agent.vector_db_path`` 读取数据库路径;
    若为相对路径则基于项目根目录解析.

    Args:
        config: 项目配置.  为 None 时自动加载 config.yaml.

    Returns:
        已初始化的 VectorStore 实例.  调用方负责关闭资源
        （推荐使用 ``with`` 语句）.
    """
    if config is None:
        config = load_config()

    db_path = Path(config.agent.vector_db_path)
    if not db_path.is_absolute():
        db_path = config.root_path / db_path

    # 将 chroma 路径替换为 qdrant 路径（兼容旧配置）
    db_path_str = str(db_path)
    if "chroma" in db_path_str:
        db_path = Path(db_path_str.replace("chroma", "qdrant"))

    return VectorStore(str(db_path))

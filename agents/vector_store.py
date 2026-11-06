"""Weaviate vector store wrapper for knowledge graph semantic search.

Provides a thin abstraction over weaviate-client for upserting,
searching, and managing knowledge entry embeddings with local
embedded persistence.  Supports filtered search by domain, type,
depth, status, and other metadata fields.  Includes native hybrid
search combining BM25 keyword and vector similarity.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import weaviate
import weaviate.classes as wvc
from weaviate.classes.query import Filter, HybridFusion, MetadataQuery

from agents.config import AgentConfig, ProjectConfig, load_config

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "KnowledgeEntries"
_BATCH_SIZE = 100


def _get_vector_dim() -> int:
    """Get vector dimension from config (supports both local and Gemini embeddings)."""
    try:
        config = load_config()
        return config.agent.embedding_dim
    except Exception:
        return 384  # fallback to local model dimension


_VECTOR_DIM = _get_vector_dim()


def _entry_id_to_uuid(entry_id: str) -> str:
    """将 entry_id 转换为确定性 UUID.

    使用 uuid5(NAMESPACE_DNS, entry_id) 生成稳定的 UUID，
    确保同一 entry_id 始终映射到同一 UUID.

    Args:
        entry_id: 知识条目 ID，如 'ke-20260226-goroutine-scheduling'.

    Returns:
        UUID 字符串.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, entry_id))


def _build_collection_properties() -> list[wvc.config.Property]:
    """构建知识条目的 Weaviate 属性 schema."""
    return [
        wvc.config.Property(
            name="entry_id",
            data_type=wvc.config.DataType.TEXT,
            skip_vectorization=True,
            tokenization=wvc.config.Tokenization.FIELD,
        ),
        wvc.config.Property(
            name="title",
            data_type=wvc.config.DataType.TEXT,
        ),
        wvc.config.Property(
            name="domain",
            data_type=wvc.config.DataType.TEXT_ARRAY,
        ),
        wvc.config.Property(
            name="tags",
            data_type=wvc.config.DataType.TEXT_ARRAY,
        ),
        wvc.config.Property(
            name="type",
            data_type=wvc.config.DataType.TEXT,
            skip_vectorization=True,
            tokenization=wvc.config.Tokenization.FIELD,
        ),
        wvc.config.Property(
            name="depth",
            data_type=wvc.config.DataType.TEXT,
            skip_vectorization=True,
            tokenization=wvc.config.Tokenization.FIELD,
        ),
        wvc.config.Property(
            name="status",
            data_type=wvc.config.DataType.TEXT,
            skip_vectorization=True,
            tokenization=wvc.config.Tokenization.FIELD,
        ),
        wvc.config.Property(
            name="confidence",
            data_type=wvc.config.DataType.NUMBER,
        ),
        wvc.config.Property(
            name="file_path",
            data_type=wvc.config.DataType.TEXT,
            skip_vectorization=True,
            tokenization=wvc.config.Tokenization.FIELD,
        ),
        wvc.config.Property(
            name="content_hash",
            data_type=wvc.config.DataType.TEXT,
            skip_vectorization=True,
            tokenization=wvc.config.Tokenization.FIELD,
        ),
        wvc.config.Property(
            name="content",
            data_type=wvc.config.DataType.TEXT,
        ),
    ]


class VectorStore:
    """Weaviate 向量数据库封装，提供知识条目的向量存储与检索能力.

    支持本地嵌入式持久化，基于余弦相似度的向量搜索，原生混合搜索
    （BM25 + 向量），以及按 domain / type / depth / status 等元数据
    字段的过滤查询.

    典型用法::

        with VectorStore("/path/to/weaviate_db") as store:
            store.init_collection()
            store.upsert_entries(entries, embeddings)
            results = store.search(query_vec, top_k=5)
    """

    def __init__(self, db_path: str, config: AgentConfig | None = None) -> None:
        """初始化嵌入式 Weaviate 客户端.

        Args:
            db_path: Weaviate 数据库在磁盘上的存储路径.
            config: Agent 配置，用于读取端口等参数.
        """
        self._db_path = db_path
        Path(db_path).mkdir(parents=True, exist_ok=True)

        port = config.weaviate_port if config else 8079
        grpc_port = config.weaviate_grpc_port if config else 50050

        self._client = weaviate.connect_to_embedded(
            persistence_data_path=db_path,
            port=port,
            grpc_port=grpc_port,
        )
        self._collection = None  # lazily resolved
        self._config = config

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭 Weaviate 客户端连接，释放底层资源."""
        if self._client is not None:
            self._client.close()
            self._client = None  # type: ignore[assignment]

    def __enter__(self) -> VectorStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_collection(self):
        """获取 collection 引用（懒加载）."""
        if self._collection is None:
            self._collection = self._client.collections.get(_COLLECTION_NAME)
        return self._collection

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def init_collection(self, config: AgentConfig | None = None) -> None:
        """创建或重建向量集合.

        若同名集合已存在则先删除，随后重新创建.
        适用于全量重建索引的场景.
        """
        cfg = config or self._config

        if self._client.collections.exists(_COLLECTION_NAME):
            self._client.collections.delete(_COLLECTION_NAME)
            logger.info("已删除旧集合: %s", _COLLECTION_NAME)

        bm25_b = cfg.bm25_b if cfg else 0.75
        bm25_k1 = cfg.bm25_k1 if cfg else 1.5

        self._client.collections.create(
            name=_COLLECTION_NAME,
            vectorizer_config=wvc.config.Configure.Vectorizer.none(),
            vector_index_config=wvc.config.Configure.VectorIndex.hnsw(
                distance_metric=wvc.config.VectorDistances.COSINE,
            ),
            inverted_index_config=wvc.config.Configure.inverted_index(
                bm25_b=bm25_b,
                bm25_k1=bm25_k1,
            ),
            properties=_build_collection_properties(),
        )
        self._collection = None  # force re-fetch
        logger.info(
            "已创建集合: %s (维度=%d, 距离=Cosine, BM25 b=%.2f k1=%.2f)",
            _COLLECTION_NAME, _VECTOR_DIM, bm25_b, bm25_k1,
        )

    def ensure_collection(self) -> bool:
        """非破坏性创建集合：已存在则跳过.

        Returns:
            True 表示集合已存在（增量模式可用），False 表示新创建.
        """
        if self._client.collections.exists(_COLLECTION_NAME):
            logger.info("集合已存在: %s", _COLLECTION_NAME)
            return True

        self.init_collection()
        return False

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def upsert_entries(
        self,
        entries: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> int:
        """批量写入知识条目及其向量到 Weaviate.

        每个条目包含 ``metadata`` 字典（含 id / title / domain / tags
        等字段）和 ``content`` 正文.  向量与条目按下标一一对应.

        Args:
            entries: 条目列表，每项包含 ``metadata`` (dict) 和
                     ``content`` (str).
            embeddings: 与 entries 等长的嵌入向量列表.

        Returns:
            成功写入的 object 数量.

        Raises:
            ValueError: entries 与 embeddings 长度不匹配时抛出.
        """
        if len(entries) != len(embeddings):
            raise ValueError(
                f"条目数量 ({len(entries)}) 与向量数量 ({len(embeddings)}) 不匹配"
            )

        if not entries:
            return 0

        collection = self._get_collection()
        objects: list[wvc.data.DataObject] = []

        for entry, vector in zip(entries, embeddings, strict=True):
            meta = entry.get("metadata", {})
            entry_id = str(meta.get("id", ""))
            if not entry_id:
                logger.warning("跳过缺少 id 的条目: %s", meta.get("title", "?"))
                continue

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

            properties = {
                "entry_id": entry_id,
                "title": str(meta.get("title", "")),
                "domain": domain_list,
                "tags": tags_list,
                "type": str(meta.get("type", "")),
                "depth": str(meta.get("depth", "")),
                "status": str(meta.get("status", "")),
                "confidence": float(meta.get("confidence") or 0.0),
                "file_path": str(meta.get("file_path", "")),
                "content_hash": str(meta.get("content_hash", "")),
                "content": entry.get("content", ""),
            }

            objects.append(
                wvc.data.DataObject(
                    uuid=_entry_id_to_uuid(entry_id),
                    properties=properties,
                    vector=vector,
                )
            )

        # 分批 upsert
        upserted = 0
        for i in range(0, len(objects), _BATCH_SIZE):
            batch = objects[i: i + _BATCH_SIZE]
            collection.data.insert_many(batch)
            upserted += len(batch)
            logger.debug(
                "已写入第 %d-%d 条 (共 %d)",
                i + 1, i + len(batch), len(objects),
            )

        logger.info("upsert 完成: %d 个 object 已写入", upserted)
        return upserted

    # ------------------------------------------------------------------
    # Incremental sync helpers
    # ------------------------------------------------------------------

    def get_all_payloads(self) -> dict[str, dict[str, Any]]:
        """遍历所有 object 的属性（不加载 vector）.

        Returns:
            {entry_id: properties_dict} 映射.
        """
        result: dict[str, dict[str, Any]] = {}
        collection = self._get_collection()

        for item in collection.iterator(include_vector=False):
            props = item.properties
            eid = props.get("entry_id", "")
            if eid:
                result[eid] = props

        return result

    def delete_points(self, entry_ids: list[str]) -> int:
        """按 entry_id 批量删除 object.

        Args:
            entry_ids: 要删除的条目 ID 列表.

        Returns:
            删除的 object 数量.
        """
        if not entry_ids:
            return 0

        collection = self._get_collection()
        # 按 UUID 批量删除
        uuids = [_entry_id_to_uuid(eid) for eid in entry_ids]
        for uid in uuids:
            try:
                collection.data.delete_by_id(uid)
            except Exception:
                logger.debug("删除 %s 时出错（可能已不存在）", uid)

        logger.info("已删除 %d 个 object", len(entry_ids))
        return len(entry_ids)

    # ------------------------------------------------------------------
    # Search — vector only
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """基于向量相似度搜索知识条目，支持元数据过滤.

        Args:
            query_embedding: 查询文本的嵌入向量.
            top_k: 返回的最大结果数.
            filters: 可选的元数据过滤条件.

        Returns:
            结果列表，每项包含 entry_id / title / score / metadata.
        """
        collection = self._get_collection()
        weaviate_filter = self._build_filter(filters) if filters else None

        response = collection.query.near_vector(
            near_vector=query_embedding,
            filters=weaviate_filter,
            limit=top_k,
            return_metadata=MetadataQuery(distance=True),
        )

        results: list[dict[str, Any]] = []
        for obj in response.objects:
            props = obj.properties
            # Weaviate cosine distance → similarity: 1 - distance
            score = 1.0 - (obj.metadata.distance or 0.0)
            results.append({
                "entry_id": props.get("entry_id", ""),
                "title": props.get("title", ""),
                "score": score,
                "metadata": props,
            })

        return results

    # ------------------------------------------------------------------
    # Hybrid search — Weaviate native
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        alpha: float = 0.7,
        top_k: int = 5,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Weaviate 原生混合搜索：BM25 关键词 + 向量语义.

        Args:
            query: 原始查询文本（用于 BM25）.
            query_embedding: 查询文本的嵌入向量.
            alpha: 向量权重 (0.0=纯BM25, 1.0=纯向量).
            top_k: 返回的最大结果数.
            filters: 可选的元数据过滤条件.

        Returns:
            结果列表，每项包含 entry_id / title / score / metadata.
        """
        collection = self._get_collection()
        weaviate_filter = self._build_filter(filters) if filters else None

        response = collection.query.hybrid(
            query=query,
            vector=query_embedding,
            alpha=alpha,
            query_properties=["title^3", "tags^2", "content"],
            fusion_type=HybridFusion.RELATIVE_SCORE,
            filters=weaviate_filter,
            limit=top_k,
            return_metadata=MetadataQuery(score=True, explain_score=True),
        )

        results: list[dict[str, Any]] = []
        for obj in response.objects:
            props = obj.properties
            results.append({
                "entry_id": props.get("entry_id", ""),
                "title": props.get("title", ""),
                "score": obj.metadata.score or 0.0,
                "explain_score": obj.metadata.explain_score or "",
                "metadata": props,
            })

        return results

    # ------------------------------------------------------------------
    # Similarity search
    # ------------------------------------------------------------------

    def search_similar_to(
        self,
        entry_id: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """查找与指定条目相似的其他知识条目.

        Args:
            entry_id: 目标条目的 ID.
            top_k: 返回的最大结果数.

        Returns:
            结果列表，每项包含 entry_id / title / score.
        """
        collection = self._get_collection()
        obj_uuid = _entry_id_to_uuid(entry_id)

        try:
            obj = collection.query.fetch_object_by_id(
                obj_uuid, include_vector=True,
            )
        except Exception:
            logger.warning("未找到条目: %s", entry_id)
            return []

        if obj is None or obj.vector is None:
            logger.warning("条目 %s 缺少向量数据", entry_id)
            return []

        # 获取向量（可能是 dict 或 list）
        source_vector = obj.vector
        if isinstance(source_vector, dict):
            source_vector = source_vector.get("default", [])

        response = collection.query.near_vector(
            near_vector=source_vector,
            limit=top_k + 1,
            return_metadata=MetadataQuery(distance=True),
        )

        results: list[dict[str, Any]] = []
        for r in response.objects:
            props = r.properties
            hit_id = props.get("entry_id", "")
            if hit_id == entry_id:
                continue
            score = 1.0 - (r.metadata.distance or 0.0)
            results.append({
                "entry_id": hit_id,
                "title": props.get("title", ""),
                "score": score,
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

        Over-fetches and post-filters since Weaviate TEXT_ARRAY
        negation support is limited.

        Args:
            query_embedding: The query vector.
            exclude_domains: Domain keys to exclude from results.
            top_k: Maximum number of results.

        Returns:
            List of result dicts with entry_id, title, score, metadata.
        """
        collection = self._get_collection()

        response = collection.query.near_vector(
            near_vector=query_embedding,
            limit=top_k * 3,  # over-fetch to compensate for filtering
            return_metadata=MetadataQuery(distance=True),
        )

        results: list[dict[str, Any]] = []
        for obj in response.objects:
            props = obj.properties
            obj_domains = props.get("domain", [])
            if any(d in exclude_domains for d in obj_domains):
                continue
            score = 1.0 - (obj.metadata.distance or 0.0)
            results.append({
                "entry_id": props.get("entry_id", ""),
                "title": props.get("title", ""),
                "score": score,
                "metadata": props,
            })
            if len(results) >= top_k:
                break

        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """返回集合统计信息.

        Returns:
            包含 points_count / status 等键的字典.
        """
        if not self._client.collections.exists(_COLLECTION_NAME):
            return {
                "collection": _COLLECTION_NAME,
                "exists": False,
                "points_count": 0,
            }

        collection = self._get_collection()
        agg = collection.aggregate.over_all(total_count=True)
        return {
            "collection": _COLLECTION_NAME,
            "exists": True,
            "points_count": agg.total_count,
            "vector_dim": _VECTOR_DIM,
            "distance": "Cosine",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter(
        filters: dict[str, str],
    ) -> Filter:
        """将 {field: value} 字典转换为 Weaviate Filter 对象.

        domain / tags 字段使用 contains_any（因为属性为数组），
        其余字段使用 equal 精确匹配.
        """
        conditions: list[Filter] = []

        for field_name, value in filters.items():
            if not value:
                continue

            if field_name in ("domain", "tags"):
                conditions.append(
                    Filter.by_property(field_name).contains_any([value])
                )
            else:
                conditions.append(
                    Filter.by_property(field_name).equal(value)
                )

        if not conditions:
            return None  # type: ignore[return-value]

        if len(conditions) == 1:
            return conditions[0]

        # AND all conditions together
        result = conditions[0]
        for c in conditions[1:]:
            result = result & c
        return result


# ======================================================================
# Module-level factory
# ======================================================================


def get_vector_store(config: ProjectConfig | None = None) -> VectorStore:
    """工厂函数: 根据项目配置创建 VectorStore 实例.

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

    # 兼容旧路径名
    db_path_str = str(db_path)
    if "chroma" in db_path_str:
        db_path = Path(db_path_str.replace("chroma", "weaviate"))
    if "qdrant" in db_path_str:
        db_path = Path(db_path_str.replace("qdrant", "weaviate"))

    return VectorStore(str(db_path), config=config.agent)

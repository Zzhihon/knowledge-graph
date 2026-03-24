from __future__ import annotations

from pathlib import Path

import frontmatter

ROOT = Path(__file__).resolve().parent.parent
INTERVIEW_DIR = ROOT / "09-Interview"

KEYWORD_TAGS = {
    "grpc": ["grpc", "http2", "protobuf", "rpc"],
    "protobuf": ["protobuf", "idl"],
    "http/2": ["http2"],
    "hls": ["hls", "streaming-media"],
    "websocket": ["websocket", "realtime"],
    "tcp": ["tcp"],
    "tls": ["tls"],
    "jwt": ["jwt", "auth"],
    "oauth": ["oauth", "auth"],
    "sso": ["sso", "auth"],
    "raft": ["raft", "mit6.824", "consensus"],
    "etcd": ["etcd", "raft", "mit6.824", "consensus"],
    "sentinel": ["redis-sentinel", "failover", "high-availability"],
    "redis": ["redis"],
    "zset": ["redis-zset", "sorted-set"],
    "pipeline": ["redis-pipeline"],
    "postgresql": ["postgresql"],
    "mvcc": ["mvcc"],
    "mysql": ["mysql"],
    "联合索引": ["mysql-index", "query-optimization"],
    "filesort": ["filesort", "query-optimization"],
    "rabbitmq": ["rabbitmq", "message-queue"],
    "kafka": ["kafka", "message-queue"],
    "消息队列": ["message-queue"],
    "队列": ["message-queue"],
    "幂等": ["idempotency"],
    "削峰": ["traffic-shaping", "peak-shaving"],
    "死信": ["dlq", "dead-letter-queue"],
    "重试": ["retry"],
    "回放": ["replay"],
    "乱序": ["message-ordering"],
    "goroutine": ["goroutine", "concurrency"],
    "gmp": ["gmp", "golang-runtime"],
    "channel": ["channel", "concurrency"],
    "cgo": ["cgo", "performance-optimization"],
    "go ": ["golang"],
    "golang": ["golang"],
    "k8s": ["kubernetes"],
    "kubernetes": ["kubernetes"],
    "调度器": ["k8s-scheduler"],
    "deployment": ["k8s-deployment"],
    "cilium": ["cilium", "cni", "ebpf"],
    "calico": ["calico", "cni"],
    "prometheus": ["prometheus", "observability"],
    "opentelemetry": ["opentelemetry", "tracing", "observability"],
    "kaniko": ["kaniko", "container-build"],
    "helm": ["helm"],
    "kustomize": ["kustomize"],
    "cloudnativepg": ["cloudnativepg", "postgresql", "database-ops"],
    "haproxy": ["haproxy", "load-balancing"],
    "keepalived": ["keepalived", "high-availability"],
    "日志": ["logging"],
    "工作流": ["workflow"],
    "temporal": ["temporal", "workflow"],
    "dapr": ["dapr", "event-driven"],
    "rss": ["rss", "ingestion-pipeline"],
    "知识图谱": ["knowledge-graph"],
    "图谱": ["knowledge-graph"],
    "模型编排": ["model-orchestration", "llm"],
    "json": ["json"],
    "缓存": ["cache"],
    "雪崩": ["service-degradation", "resilience"],
    "降级": ["degradation", "resilience"],
    "数据库迁移": ["database-migration"],
    "一致性": ["consistency"],
    "分布式": ["distributed-systems"],
}

DOMAIN_TAGS = {
    "golang": ["golang"],
    "cloud-native": ["cloud-native"],
    "distributed-systems": ["distributed-systems"],
    "networking": ["networking"],
    "message-queue": ["message-queue"],
    "databases": ["databases"],
    "ai-agent": ["ai-agent"],
    "ai-infra": ["ai-infra"],
    "frontend": ["frontend"],
}

CATEGORY_TAGS = {
    "fundamentals": ["八股", "底层原理"],
    "tech-choices": ["技术选型"],
    "real-scenarios": ["场景题"],
    "project-deep-dive": ["项目拷打"],
}

PROJECT_TAGS = {
    "yongtu-intern": ["用途科技", "实习项目"],
    "smart-portal": ["智慧服务门户", "校园系统"],
    "cloud-native-infra": ["云原生平台", "基础设施"],
    "1qfm": ["1qfm", "音频平台"],
    "knowledge-graph": ["知识图谱", "ai-agent项目"],
}


def normalize(text: str) -> str:
    return text.lower()


def _extract_signal_text(post: frontmatter.Post) -> str:
    """Use only high-signal sections to avoid over-tagging from long answers.

    We intentionally ignore most of the Answer / Follow-ups body because it often
    mentions adjacent technologies for comparison, which causes false-positive tags.
    """
    lines = post.content.splitlines()
    kept: list[str] = []
    in_key_points = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('# '):
            kept.append(stripped)
        elif stripped.startswith('## Question'):
            kept.append(stripped)
        elif stripped.startswith('>'):
            kept.append(stripped)
        elif stripped.startswith('## Key Points'):
            in_key_points = True
            kept.append(stripped)
        elif stripped.startswith('## Answer') or stripped.startswith('## Follow-ups'):
            in_key_points = False
        elif in_key_points and stripped.startswith('- '):
            kept.append(stripped)
    return '\n'.join(kept)


def infer_tags(post: frontmatter.Post) -> list[str]:
    meta = dict(post.metadata)
    text = normalize(
        f"{meta.get('title', '')}\n{_extract_signal_text(post)}"
    )
    tags = set()
    for category_tag in CATEGORY_TAGS.get(str(meta.get("category", "")), []):
        tags.add(category_tag)

    project = meta.get("project")
    if project:
        for t in PROJECT_TAGS.get(str(project), []):
            tags.add(t)

    domains = meta.get("domain", [])
    if isinstance(domains, str):
        domains = [domains]
    for domain in domains:
        for t in DOMAIN_TAGS.get(str(domain), []):
            tags.add(t)

    for keyword, inferred in KEYWORD_TAGS.items():
        if keyword in text:
            for t in inferred:
                tags.add(t)

    # Cleanups / aliases
    if "grpc" in tags:
        tags.add("微服务通信")
    if "mit6.824" in tags or "raft" in tags:
        tags.add("分布式一致性")
    if "rabbitmq" in tags or "kafka" in tags:
        tags.add("消息中间件")
    if "kubernetes" in tags or "cloud-native" in tags:
        tags.add("云原生")

    return sorted(tags, key=lambda s: s.lower())


def main() -> None:
    updated = 0
    for path in sorted(INTERVIEW_DIR.glob("*.md")):
        post = frontmatter.load(path)
        old_tags = list(post.metadata.get("tags", []))
        new_tags = infer_tags(post)
        if new_tags != old_tags:
            post.metadata["tags"] = new_tags
            path.write_text(frontmatter.dumps(post), encoding="utf-8")
            updated += 1
            print(f"updated {path.name}: +{len(set(new_tags) - set(old_tags))} tags")
    print(f"done updated={updated}")


if __name__ == "__main__":
    main()

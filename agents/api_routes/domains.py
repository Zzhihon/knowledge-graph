"""Domain overview API routes."""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from ..config import load_config
from ..utils import load_entries
from ..radar import compute_domain_strength

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("/overview")
async def get_domains_overview() -> Dict[str, Any]:
    """Get comprehensive overview of all domains with metrics and entries."""
    try:
        config = load_config()
        all_entries = load_entries(config.vault_path)

        # Group entries by domain
        domain_entries: Dict[str, List[Dict[str, Any]]] = {}
        for entry in all_entries:
            meta = entry.get("metadata", {})
            entry_domains = meta.get("domain", [])
            if isinstance(entry_domains, str):
                entry_domains = [entry_domains]

            for domain_key in entry_domains:
                if domain_key not in domain_entries:
                    domain_entries[domain_key] = []

                domain_entries[domain_key].append({
                    "id": meta.get("id", ""),
                    "title": meta.get("title", ""),
                    "type": meta.get("type", ""),
                    "depth": meta.get("depth", ""),
                    "confidence": meta.get("confidence", 0.0),
                    "status": meta.get("status", ""),
                    "domain": entry_domains,
                    "created": meta.get("created", ""),
                    "updated": meta.get("updated", ""),
                })

        # Build domain overview list
        domains_overview = []
        for domain_key, domain_config in config.domains.items():
            # Compute metrics for this domain
            metrics = compute_domain_strength(domain_key, all_entries, config)

            # Get entries for this domain
            entries = domain_entries.get(domain_key, [])

            domains_overview.append({
                "key": domain_key,
                "label": domain_config.label,
                "icon": domain_config.icon,
                "sub_domains": domain_config.sub_domains,
                "metrics": {
                    "coverage": metrics["coverage"],
                    "depth_score": metrics["depth_score"],
                    "freshness": metrics["freshness"],
                    "avg_confidence": metrics["avg_confidence"],
                    "total_entries": metrics["total_entries"]
                },
                "entries": entries
            })

        return {"domains": domains_overview}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load domain overview: {str(e)}")

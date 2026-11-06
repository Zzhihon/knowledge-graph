"""Alerting and self-healing for control-plane incidents (#9).

    plane.incident.opened / closed ─▶ kg plane notify --autoloop
        ├─ card per incident (once; silenced ones skipped), recovery notice with duration
        ├─ actions per incident type: suggest by default, run only when heal.yaml says auto
        │    egress.outage  → pause rules that require `egress`, resume + catch up on close
        │    service.down   → launchctl kickstart once, escalate if still down 10 minutes later
        │    index.*        → mark the search index stale
        ├─ every action audited in .kg/plane/heal-audit.jsonl, undone with `kg plane heal undo`
        └─ on close (long or repeated): retro input + incident.retro.ready for a Claude rule

Everything that touches the hub goes through `hub_actions.HubActions`, so tests and the drill
use a fake or a local stub; the owner key is read on the server side only.
"""

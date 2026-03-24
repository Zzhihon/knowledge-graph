from __future__ import annotations

from agents.config import load_config
from agents.interview import generate_interview_questions


def run_batch(category: str | None, project: str | None, skill_domain: str | None, count: int) -> tuple[int, int]:
    created = 0
    failed = 0
    label = f"category={category or 'ALL'}, project={project or '-'}, skill={skill_domain or '-'}, count={count}"
    print(f"\n=== START {label} ===", flush=True)
    for event in generate_interview_questions(
        category=category,
        project=project,
        skill_domain=skill_domain,
        count=count,
    ):
        et = event.get("event")
        if et == "start":
            print(f"[start] total_expected={event.get('total_expected')} model={event.get('model')}", flush=True)
        elif et == "question_done":
            created += 1
            print(f"[ok] {event.get('title')} ({event.get('category')}/{event.get('difficulty')})", flush=True)
        elif et == "error":
            failed += 1
            print(f"[err] {event.get('message')}", flush=True)
        elif et == "complete":
            print(f"[complete] created={event.get('total_created')} failed={event.get('total_failed')}", flush=True)
    return created, failed


def main() -> None:
    load_config()  # validate config early

    # Target: add ~50 high-coverage questions
    # Distribution:
    # - 20 skill-domain focused (deep fundamentals/choices)
    # - 20 project-focused (real/project deep dive)
    # - 10 mixed infra / system topics
    plans = [
        # Skill-domain focused
        ("fundamentals", None, "golang", 4),
        ("fundamentals", None, "cloud-native", 4),
        ("fundamentals", None, "distributed-systems", 4),
        ("fundamentals", None, "networking", 4),
        ("fundamentals", None, "message-queue", 4),

        # Tech choices across domains
        ("tech-choices", None, "cloud-native", 3),
        ("tech-choices", None, "distributed-systems", 3),
        ("tech-choices", None, "message-queue", 3),
        ("tech-choices", None, "networking", 3),

        # Project deep dives
        ("project-deep-dive", "yongtu-intern", None, 4),
        ("project-deep-dive", "smart-portal", None, 4),
        ("project-deep-dive", "cloud-native-infra", None, 4),
        ("project-deep-dive", "knowledge-graph", None, 4),

        # Real scenarios
        ("real-scenarios", "cloud-native-infra", None, 3),
        ("real-scenarios", None, "golang", 2),
        ("real-scenarios", None, "distributed-systems", 2),
        ("real-scenarios", None, "message-queue", 2),
    ]

    total_created = 0
    total_failed = 0
    for category, project, skill_domain, count in plans:
        created, failed = run_batch(category, project, skill_domain, count)
        total_created += created
        total_failed += failed

    print("\n=== ALL DONE ===", flush=True)
    print(f"total_created={total_created}", flush=True)
    print(f"total_failed={total_failed}", flush=True)


if __name__ == "__main__":
    main()

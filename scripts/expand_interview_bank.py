from __future__ import annotations

from agents.interview import generate_interview_questions, get_interview_stats


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
    current = get_interview_stats()["total_questions"]
    print(f"current_total={current}", flush=True)

    # Aim: expand breadth, especially scenario/skill-domain coverage.
    plans = [
        # Fundamentals by skill domain
        ("fundamentals", None, "message-queue", 3),
        ("fundamentals", None, "networking", 3),
        ("fundamentals", None, "golang", 3),
        ("fundamentals", None, "distributed-systems", 3),

        # Real scenarios — currently underrepresented
        ("real-scenarios", None, "networking", 3),
        ("real-scenarios", None, "message-queue", 3),
        ("real-scenarios", None, "golang", 3),
        ("real-scenarios", None, "cloud-native", 3),
        ("real-scenarios", None, "distributed-systems", 3),

        # Tech choices breadth
        ("tech-choices", None, "message-queue", 2),
        ("tech-choices", None, "networking", 2),
        ("tech-choices", None, "golang", 2),

        # Project depth for weaker projects
        ("project-deep-dive", "smart-portal", None, 3),
        ("project-deep-dive", "1qfm", None, 3),
        ("project-deep-dive", "cloud-native-infra", None, 2),

        # Scenario/project crossover
        ("real-scenarios", "smart-portal", None, 2),
        ("real-scenarios", "yongtu-intern", None, 2),
    ]

    total_created = 0
    total_failed = 0
    for category, project, skill_domain, count in plans:
        created, failed = run_batch(category, project, skill_domain, count)
        total_created += created
        total_failed += failed

    final_total = get_interview_stats()["total_questions"]
    print("\n=== EXPAND DONE ===", flush=True)
    print(f"total_created={total_created}", flush=True)
    print(f"total_failed={total_failed}", flush=True)
    print(f"final_total={final_total}", flush=True)


if __name__ == "__main__":
    main()

import pytest

from kg.eval.retrieval import Case, _percentile, _questions, score
from kg.retrieval.models import query_prompt


def test_score_metrics() -> None:
    cases = [
        Case("a", ["x"]),  # hit at rank 1
        Case("b", ["y", "z"]),  # y at rank 3, z missing
        Case("c", ["w"]),  # missing
        Case("d", []),  # unlabelled: not counted
    ]
    ranked = {"a": ["x", "q"], "b": ["q", "r", "y"], "c": ["q"], "d": ["x"]}
    result = score("fake", lambda q, k: ranked[q], cases)
    assert result.cases == 3
    assert result.hit1 == 1 / 3
    assert result.recall5 == (1 + 0.5 + 0) / 3
    assert abs(result.mrr10 - (1 + 1 / 3) / 3) < 1e-9
    assert [m["q"] for m in result.misses] == ["c"]


def test_score_reports_per_query_latency_without_errors(monkeypatch) -> None:
    # run start, then each call: 10 ms, 30 ms, then one that errors
    ticks = iter([0.0, 0.0, 0.010, 1.0, 1.030, 2.0, 2.100])
    monkeypatch.setattr("kg.eval.retrieval.time.monotonic", lambda: next(ticks, 9.0))

    def run(q: str, k: int) -> list[str]:
        if q == "c":
            raise RuntimeError("boom")
        return ["x"]

    result = score("timed", run, [Case("a", ["x"]), Case("b", ["x"]), Case("c", ["x"])])
    assert [c["ms"] for c in result.per_case][:2] == [10.0, 30.0]
    assert (result.p50_ms, result.p95_ms) == (10.0, 30.0)  # the failed call is left out


def test_percentile_is_nearest_rank() -> None:
    assert _percentile([], 0.5) is None
    assert _percentile([5.0], 0.95) == 5.0
    values = [float(v) for v in range(1, 21)]
    assert (_percentile(values, 0.5), _percentile(values, 0.95)) == (10.0, 19.0)


def test_qwen3_query_gets_an_english_instruction(monkeypatch) -> None:
    monkeypatch.delenv("KG_QUERY_INSTRUCT", raising=False)
    prompt = query_prompt("Qwen/Qwen3-Embedding-0.6B")
    assert prompt.startswith("Instruct: Given a question") and prompt.endswith("\nQuery:")
    assert query_prompt("BAAI/bge-m3") == ""
    monkeypatch.setenv("KG_QUERY_INSTRUCT", "")
    assert query_prompt("Qwen/Qwen3-Embedding-0.6B") == ""


@pytest.mark.parametrize("task", ["Find notes"])
def test_query_instruction_can_be_overridden(monkeypatch, task) -> None:
    monkeypatch.setenv("KG_QUERY_INSTRUCT", task)
    assert query_prompt("BAAI/bge-m3") == f"Instruct: {task}\nQuery:"


def test_broken_retriever_scores_zero() -> None:
    def broken(q: str, k: int) -> list[str]:
        raise RuntimeError("embedding failed")

    result = score("broken", broken, [Case("a", ["x"])])
    assert result.errors == 1 and result.recall10 == 0
    assert "embedding failed" in result.misses[0]["error"]


def test_questions_skip_secrets_links_and_instructions() -> None:
    text = (
        "为什么副本顶满了负载却不高？\n"
        "deepseek: sk-abcdefghijklmnop1234 这个怎么配？\n"
        "文档在 https://example.com/x 吗\n"
        "sudo 密码是 hunter2，你记一下怎么用\n"
        "继续\n"
        "帮我看一下为什么挂了"
    )
    assert list(_questions(text)) == ["为什么副本顶满了负载却不高？"]

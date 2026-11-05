from kg.eval.retrieval import Case, _questions, score


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

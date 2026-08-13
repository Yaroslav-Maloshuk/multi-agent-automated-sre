"""RAGAS evaluation tests: lexical fallback, mocked RAGAS path, heuristics."""

from ai.eval.ragas_eval import (
    EvalResult,
    _faithfulness,
    _mean,
    _recall,
    _relevancy,
    _tokenize,
    evaluate,
)


async def test_evaluate_lexical_fallback_when_ragas_absent(monkeypatch):
    monkeypatch.setattr("ai.eval.ragas_eval._HAS_RAGAS", False)
    result = await evaluate(
        questions=["disk full?"],
        answers=["clean /var/log and rotate old logs"],
        contexts=[["clean /var/log and rotate old logs"]],
        ground_truths=[["clean /var/log"]],
    )
    assert result.framework == "lexical"
    assert result.samples == 1
    assert 0.0 <= result.faithfulness <= 1.0
    assert 0.0 <= result.answer_relevancy <= 1.0
    assert 0.0 <= result.context_recall <= 1.0
    assert result.detail[0]["question"] == "disk full?"
    assert set(result.detail[0]) == {"question", "faithfulness", "answer_relevancy", "context_recall"}


async def test_evaluate_delegates_to_ragas_when_installed(monkeypatch):
    captured = {}

    async def fake_ragas(questions, answers, contexts, ground_truths):
        captured["args"] = (questions, answers, contexts, ground_truths)
        return EvalResult(
            samples=len(questions),
            framework="ragas",
            faithfulness=1.0,
            answer_relevancy=0.9,
            context_recall=0.8,
        )

    monkeypatch.setattr("ai.eval.ragas_eval._HAS_RAGAS", True)
    monkeypatch.setattr("ai.eval.ragas_eval._ragas_evaluate", fake_ragas)
    result = await evaluate(["q1"], ["a1"], [["c1"]], [["g1"]])
    assert result.framework == "ragas"
    assert result.faithfulness == 1.0
    assert captured["args"] == (["q1"], ["a1"], [["c1"]], [["g1"]])


async def test_evaluate_falls_back_to_lexical_when_ragas_raises(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("llm judge unavailable")

    monkeypatch.setattr("ai.eval.ragas_eval._HAS_RAGAS", True)
    monkeypatch.setattr("ai.eval.ragas_eval._ragas_evaluate", boom)
    result = await evaluate(["q"], ["a"], [["c"]], [["g"]])
    assert result.framework == "lexical"
    assert result.samples == 1


def test_faithfulness_grounded_in_context():
    assert _faithfulness("clean /var/log", ["clean /var/log"]) == 1.0
    assert _faithfulness("restart the pod", ["clean /var/log"]) < 0.5
    assert _faithfulness("", ["clean /var/log"]) == 0.0
    assert _faithfulness("clean /var/log", []) == 0.0


def test_recall_against_ground_truth():
    assert _recall(["clean /var/log and rotate logs"], ["clean /var/log"]) == 1.0
    assert _recall([], ["clean /var/log"]) == 0.0
    assert _recall(["clean /var/log"], []) == 0.0


def test_relevancy_question_answer_overlap():
    assert _relevancy("disk full on node", "disk full cause log rotation") > 0.5
    assert _relevancy("disk full", "restart the pod") == 0.5
    assert _relevancy("disk full", "") == 0.0


def test_mean_and_tokenize():
    assert _mean([0.5, 1.0]) == 0.75
    assert _mean([]) == 0.0
    tokens = _tokenize("Disk full on node!")
    assert "disk" in tokens
    assert len(tokens) == 4


async def test_ragas_framework_reports_mean_metrics(monkeypatch):
    monkeypatch.setattr("ai.eval.ragas_eval._HAS_RAGAS", False)
    result = await evaluate(
        questions=["a", "b"],
        answers=["clean /var/log", "clean /var/log"],
        contexts=[["clean /var/log"], ["clean /var/log"]],
        ground_truths=[["clean /var/log"], ["clean /var/log"]],
    )
    assert result.framework == "lexical"
    assert result.samples == 2
    assert result.faithfulness == 1.0
    assert result.context_recall == 1.0

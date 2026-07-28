import json
from pathlib import Path

from app.models import EvaluationCaseResult, EvaluationSummary
from app.rag.store import retrieval_mode, search


DATASET = Path(__file__).resolve().parent.parent / "data" / "evaluation" / "questions.json"


def load_cases() -> list[dict]:
    return json.loads(DATASET.read_text(encoding="utf-8"))


def run_evaluation(database: Path, limit: int = 5) -> EvaluationSummary:
    results = []
    for case in load_cases():
        sources = search(database, case["question"], limit)
        retrieved = [source.section for source in sources]
        ranks = [
            retrieved.index(section) + 1
            for section in case["expected_sections"]
            if section in retrieved
        ]
        best_rank = min(ranks) if ranks else None
        results.append(
            EvaluationCaseResult(
                case_id=case["id"],
                question=case["question"],
                expected_sections=case["expected_sections"],
                retrieved_sections=retrieved,
                passed=best_rank is not None,
                reciprocal_rank=round(1 / best_rank, 4) if best_rank else 0.0,
            )
        )
    passed = sum(result.passed for result in results)
    mrr = sum(result.reciprocal_rank for result in results) / len(results)
    return EvaluationSummary(
        total=len(results),
        passed=passed,
        recall_at_k=round(passed / len(results), 4),
        mean_reciprocal_rank=round(mrr, 4),
        retrieval_mode=retrieval_mode(database),
        cases=results,
    )


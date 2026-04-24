"""CLI runner for all eval scripts.

Usage:
    uv run python -m role_scout.eval.run_eval --scorer
    uv run python -m role_scout.eval.run_eval --recall
    uv run python -m role_scout.eval.run_eval --alignment
    uv run python -m role_scout.eval.run_eval --tailor
    uv run python -m role_scout.eval.run_eval --all
"""
from __future__ import annotations

import argparse
import sys

from role_scout.eval.discovery_recall_eval import run_recall_eval
from role_scout.eval.scorer_eval import load_ground_truth, run_scorer_eval


def main() -> int:
    parser = argparse.ArgumentParser(description="Role Scout eval runner")
    parser.add_argument("--scorer", action="store_true", help="Run scorer Spearman eval")
    parser.add_argument("--recall", action="store_true", help="Run discovery recall eval")
    parser.add_argument("--alignment", action="store_true", help="Run alignment LLM-judge eval (requires OPENAI_API_KEY or GOOGLE_API_KEY)")
    parser.add_argument("--tailor", action="store_true", help="Run tailor quality LLM-judge eval (requires judge API key)")
    parser.add_argument("--all", action="store_true", help="Run all evals")
    args = parser.parse_args()

    run_scorer = args.scorer or args.all
    run_recall = args.recall or args.all
    run_alignment = args.alignment or args.all
    run_tailor = args.tailor or args.all

    passed = True

    if run_scorer:
        print("Running scorer eval with fixture data...")
        gt = load_ground_truth()
        # Use human scores as predictions (perfect agreement) for smoke test
        predicted = [(job.hash_id, job.human_score) for job in gt]
        result = run_scorer_eval(predicted)
        print(f"  Spearman r={result.spearman_r:.3f} {'PASS' if result.pass_criteria else 'FAIL'}")
        if not result.pass_criteria:
            passed = False

    if run_recall:
        print("Running recall eval with fixture data...")
        gt = load_ground_truth()
        gold = [job.hash_id for job in gt[:10]]
        pipeline = [job.hash_id for job in gt[:9]]  # missing 1 — smoke test
        result = run_recall_eval(gold, pipeline)
        print(f"  Recall={result.recall:.1%} {'PASS' if result.pass_criteria else 'WARN'}")

    if run_alignment:
        print("Running alignment eval (requires judge API key)...")
        try:
            from role_scout.eval.alignment_eval import run_alignment_eval
            # Smoke test with 3 pairs — real eval requires actual JD + alignment pairs
            smoke_pairs = [
                ("Software engineer role requiring Python and distributed systems experience.",
                 "Experienced Python engineer with 6 years building distributed systems at scale."),
                ("ML Engineer with PyTorch and model deployment experience required.",
                 "ML engineer specializing in PyTorch model training and production deployment."),
                ("Senior backend engineer, Go, 5+ years, microservices architecture.",
                 "Senior engineer with 7 years of Go and microservices experience."),
            ]
            result = run_alignment_eval(smoke_pairs)
            if result is None:
                print("  SKIP — no judge API key (set OPENAI_API_KEY or GOOGLE_API_KEY)")
            else:
                status = "PASS" if result.pass_criteria else "WARN"
                print(f"  Overall mean={result.overall_mean:.2f} n_pairs={result.n_pairs} {status}")
        except Exception as exc:
            print(f"  ERROR — {exc}")
            passed = False

    if run_tailor:
        print("Running tailor eval (requires judge API key)...")
        try:
            from role_scout.eval.tailor_eval import run_tailor_eval
            # Smoke test with 3 pairs — real eval requires actual tailor outputs
            smoke_pairs = [
                (
                    {
                        "tailored_summary": "Results-driven ML engineer with 6 years of distributed training experience.",
                        "tailored_bullets": [
                            "Led migration of training infra to Kubernetes, cutting job failures by 40%.",
                            "Built feature store serving 50M predictions/day with <5ms p99 latency.",
                        ],
                        "keywords_incorporated": ["Kubernetes", "feature store", "distributed training"],
                    },
                    85,
                ),
                (
                    {
                        "tailored_summary": "Senior engineer experienced in Python and cloud infrastructure.",
                        "tailored_bullets": [
                            "Worked on various projects.",
                            "Improved system performance.",
                        ],
                        "keywords_incorporated": ["Python", "cloud"],
                    },
                    40,
                ),
                (
                    {
                        "tailored_summary": "ML platform engineer with deep expertise in LLM inference optimization.",
                        "tailored_bullets": [
                            "Reduced LLM inference latency by 60% via quantization and batching.",
                            "Designed multi-region model serving with 99.99% uptime SLA.",
                            "Open-sourced inference benchmarking toolkit used by 500+ teams.",
                        ],
                        "keywords_incorporated": ["LLM", "quantization", "inference optimization"],
                    },
                    92,
                ),
            ]
            result = run_tailor_eval(smoke_pairs)
            if result is None:
                print("  SKIP — no judge API key (set OPENAI_API_KEY or GOOGLE_API_KEY)")
            else:
                flags = len(result.disagreement_flags)
                status = "PASS" if result.pass_criteria else "WARN"
                print(f"  Overall mean={result.overall_mean:.2f} n_pairs={result.n_pairs} disagreements={flags} {status}")
        except Exception as exc:
            print(f"  ERROR — {exc}")
            passed = False

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

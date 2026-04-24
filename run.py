#!/usr/bin/env python3
"""Role Scout Phase 2 — agentic pipeline entry point.

Usage examples:
  uv run python run.py --agentic --dry-run
  uv run python run.py --agentic --auto-approve
  uv run python run.py --agentic --force-partial --source linkedin
  uv run python run.py --shadow
  uv run python run.py --serve

Run modes
---------
--agentic       Execute the LangGraph pipeline end-to-end.
--shadow        Run both linear and agentic paths; diff scored_jobs; write shadow report.
--auto-approve  Skip HiTL review; approve automatically (used by launchd schedules).
--dry-run       Full pipeline run but no DB writes (trigger_type=dry_run).
--force-partial Proceed even if ≥2 discovery sources fail.
--source NAME   Override active sources (may repeat; e.g. --source linkedin --source google).
--serve         Start the Flask dashboard on 127.0.0.1:5000.

RUN_MODE env var (in .env) also controls dispatch:
  RUN_MODE=agentic  → same as --agentic (default)
  RUN_MODE=shadow   → same as --shadow
  RUN_MODE=linear   → logs warning, falls back to agentic
"""
from __future__ import annotations

import argparse
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="role-scout",
        description="Role Scout Phase 2 agentic job search pipeline",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--agentic", action="store_true", help="Run LangGraph pipeline")
    mode.add_argument("--shadow", action="store_true", help="Run shadow mode (linear + agentic diff)")
    mode.add_argument("--serve", action="store_true", help="Start Flask dashboard (localhost only)")
    mode.add_argument("--mcp", action="store_true", help="Start MCP server on stdio")

    parser.add_argument("--auto-approve", action="store_true", help="Skip HiTL; approve automatically")
    parser.add_argument("--dry-run", action="store_true", help="Full run, no DB writes")
    parser.add_argument("--force-partial", action="store_true", help="Proceed even if ≥2 sources fail")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        metavar="NAME",
        help="Override sources (linkedin|google|trueup); repeat for multiple",
    )
    parser.add_argument(
        "--trigger-type",
        choices=["manual", "scheduled", "mcp"],
        default=None,
        dest="trigger_type",
        help="Override trigger classification (default: inferred from flags)",
    )
    return parser.parse_args(argv)


def _resolve_trigger_type(args: argparse.Namespace) -> str:
    """Determine trigger_type from CLI flags.

    Precedence:
      1. Explicit --trigger-type overrides everything.
      2. --auto-approve (without explicit --trigger-type) → "scheduled"
         (launchd always passes --auto-approve; classifying it as scheduled is correct).
      3. Default → "manual".
    """
    if args.trigger_type is not None:
        return args.trigger_type
    if args.auto_approve:
        return "scheduled"
    return "manual"


def _run_agentic(args: argparse.Namespace) -> None:
    from role_scout.runner import run_graph

    final_state = run_graph(
        auto_approve=args.auto_approve,
        dry_run=args.dry_run,
        force_partial=args.force_partial,
        trigger_type=_resolve_trigger_type(args),
    )

    exported = final_state.get("exported_count", 0)
    errors = final_state.get("errors", [])
    approved = final_state.get("human_approved")
    cancel_reason = final_state.get("cancel_reason")

    if approved:
        print(f"Pipeline complete — {exported} job(s) exported.")
    else:
        print(f"Pipeline cancelled — reason: {cancel_reason or 'unknown'}.")

    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors:
            print(f"  • {e}")

    sys.exit(0 if not errors else 1)


def _run_shadow(args: argparse.Namespace) -> None:
    from role_scout.shadow import run_shadow

    result = run_shadow(
        auto_approve=args.auto_approve,
        dry_run=args.dry_run,
        force_partial=args.force_partial,
        trigger_type=_resolve_trigger_type(args),
    )

    print(f"Shadow run complete — {result.run_id}")
    print(f"  Agentic jobs : {result.agentic_count}")
    print(f"  Linear jobs  : {result.linear_count}")
    print(f"  Disagreements: {len(result.disagreements)}")
    print(f"  Verdict      : {'PASS' if result.passed else 'WARN'}")
    if result.report_path:
        print(f"  Report       : {result.report_path}")

    sys.exit(0 if result.passed else 1)


def _run_mcp() -> None:
    from role_scout.mcp_server.server import run_server
    run_server()


def _run_serve() -> None:
    try:
        from role_scout.dashboard import create_app
    except ImportError:
        print("Dashboard not yet implemented (D8). Run --agentic instead.", file=sys.stderr)
        sys.exit(1)

    app = create_app()
    # Always bind to localhost — never 0.0.0.0
    app.run(host="127.0.0.1", port=5000, debug=False)


def main(argv: list[str] | None = None) -> None:
    import structlog

    args = _parse_args(argv)

    if args.serve:
        _run_serve()
        return
    if args.mcp:
        _run_mcp()
        return
    if args.shadow:
        _run_shadow(args)
        return

    # --agentic branch: respect RUN_MODE from settings for env-driven dispatch
    from role_scout.config import get_settings

    _log = structlog.get_logger()
    settings = get_settings()

    if settings.RUN_MODE == "shadow":
        _log.info("run_mode_shadow_dispatch", source="RUN_MODE env")
        _run_shadow(args)
    elif settings.RUN_MODE == "linear":
        _log.warning(
            "linear_mode_not_wired",
            message="linear mode not yet wired, falling back to agentic",
        )
        _run_agentic(args)
    else:
        # "agentic" or any future value — default path
        _run_agentic(args)


if __name__ == "__main__":
    main()

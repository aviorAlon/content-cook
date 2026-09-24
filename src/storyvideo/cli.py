"""Command-line control plane for the StoryVideo production runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from genkit.providers import ProviderError
from genkit.router import build_provider
from genkit.store import RunCollisionError, StoreError

from .assembly import AssemblyError
from .media import MediaValidationError
from .continuity import check_all as check_continuity_rules
from .preview import check_dialogue_clarity, check_duration_budget, render_preview
from .prompts import validate_camera_usage
from .qc import QCError
from .runtime import (
    StoryVideoBlocked,
    StoryVideoError,
    StoryVideoGatePending,
    StoryVideoRunner,
)
from .spec import SpecError, load_spec


from genkit.paths import data_root as _data_root

DEFAULT_PROJECT_ROOT = str(_data_root())


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--json", action="store_true", help="print machine-readable state")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="storyvideo",
        description="Resumable, paid-task-safe and QC-gated story-video production",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="validate and freeze an episode")
    prepare.add_argument("episode")
    _common(prepare)

    preview = commands.add_parser(
        "preview",
        help="render every shot's exact compiled prompt + duration budget; "
        "no lock, no remote call — use before prepare, edit freely, rerun",
    )
    preview.add_argument("episode")
    preview.add_argument("--only", help="comma-separated shot ids; default is every shot")
    preview.add_argument(
        "--out",
        help="write markdown to this path instead of the default "
        "<output_root>/manifests/preview.md",
    )
    preview.add_argument(
        "--stdout", action="store_true", help="print to stdout instead of writing a file"
    )
    _common(preview)

    legacy = commands.add_parser(
        "import-legacy", help="adopt and QC existing local artifacts without remote calls"
    )
    legacy.add_argument("episode")
    legacy.add_argument("--confirm-existing-artifacts", action="store_true", required=True)
    _common(legacy)

    run = commands.add_parser("run", help="advance safely to the next QC gate")
    run.add_argument("episode")
    run.add_argument("--only", help="comma-separated shot ids")
    run.add_argument(
        "--through",
        default="auto",
        choices=("auto", "frames", "videos", "assemble", "final"),
    )
    run.add_argument("--no-wait", action="store_true")
    _common(run)

    status = commands.add_parser("status", help="read durable state without remote calls")
    status.add_argument("episode")
    _common(status)

    review = commands.add_parser("review", help="record a QC decision")
    review.add_argument("episode")
    review.add_argument("gate", choices=("frames", "videos", "final"))
    review.add_argument(
        "--decision",
        required=True,
        choices=("pass", "retake", "edit_rescue", "redesign", "waive"),
    )
    review.add_argument("--shots", help="comma-separated shot ids; default is every item")
    review.add_argument("--reason", default="")
    review.add_argument("--reviewer", default="human")
    review.add_argument("--evidence", action="append", default=[])
    _common(review)

    asr = commands.add_parser(
        "asr-qc",
        help="transcribe generated videos and compare with the script lines; "
             "optionally record retake decisions for shots whose speech does not match",
    )
    asr.add_argument("episode")
    asr.add_argument("--only", help="comma-separated shot ids; default is every shot")
    asr.add_argument("--threshold", type=float, default=0.3, help="max character error rate to pass (default 0.3)")
    asr.add_argument("--model", default="small", help="faster-whisper model size (default small)")
    asr.add_argument("--record-retakes", action="store_true",
                     help="record a videos-gate retake for every failing shot (with expected/heard as the reason)")
    asr.add_argument("--include-stray", action="store_true",
                     help="also treat unexpected speech in silent shots as a failure")
    _common(asr)

    post = commands.add_parser(
        "post",
        help="post layers: narration VO (edge-tts) + full-screen phone UI inserts + notebook text + mix, "
             "composed from the rendered shot clips into <output_root>/<id>_post.mp4",
    )
    post.add_argument("episode")
    post.add_argument("--destination", help="override the output mp4 path")
    _common(post)

    retry = commands.add_parser(
        "retry-failed",
        help="reset failed frame/video jobs to prepared so the next run resubmits them "
             "(transient platform failures; moderation failures need a rewrite instead)",
    )
    retry.add_argument("episode")
    retry.add_argument("--only", help="comma-separated shot ids")
    retry.add_argument("--reason", default="retry after failed attempt")
    _common(retry)

    amend = commands.add_parser(
        "amend",
        help="accept a post-freeze edit confined to post-production blocks "
             "(post_graphics / voice_over / notes); refuses generation-affecting changes",
    )
    amend.add_argument("episode")
    _common(amend)

    reconcile = commands.add_parser(
        "reconcile", help="attach a recovered remote task id to an ambiguous submission"
    )
    reconcile.add_argument("episode")
    reconcile.add_argument("step_id")
    reconcile.add_argument("--task-id", required=True)
    _common(reconcile)

    retry = commands.add_parser(
        "retry", help="explicitly reset definitive failed jobs for another attempt"
    )
    retry.add_argument("episode")
    retry.add_argument("--steps", help="comma-separated step ids; default is every failure")
    retry.add_argument("--reason", required=True)
    _common(retry)

    abandon = commands.add_parser(
        "abandon",
        help="clear an ambiguous intent after confirming no remote task exists",
    )
    abandon.add_argument("episode")
    abandon.add_argument("step_id")
    abandon.add_argument("--reason", required=True)
    abandon.add_argument("--confirm-no-remote-task", action="store_true", required=True)
    _common(abandon)
    return parser


def _items(value: str | None, *, label: str) -> list[str] | None:
    if value is None:
        return None
    result = [item.strip() for item in value.split(",") if item.strip()]
    if not result:
        raise StoryVideoError(f"{label} list must not be empty")
    return result


def _summary(result: dict[str, Any]) -> None:
    print(f"{result['run_id']}: {result['status']}")
    if result.get("run_dir"):
        print(result["run_dir"])
    qc = result.get("qc") or {}
    gates = qc.get("gates") or {}
    if gates:
        print(
            "QC "
            + " | ".join(
                f"{name}={data.get('status', 'not_ready')}"
                for name, data in gates.items()
            )
        )


def _print(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _summary(result)


def main(argv: Sequence[str] | None = None) -> int:
    # 插件登记的来源（genkit.plugins）要在读设置 / 装配来源之前就位：命令行续等插件来源提交的在飞任务也要找得到它
    from genkit import plugins

    plugins.ensure_loaded()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            with StoryVideoRunner(args.project_root) as runner:
                spec = runner.prepare(args.episode)
                result = runner.status(spec["spec_path"] or args.episode)
            # 参考图上限按「模型设置」里的生图来源查（跟网页详情页一个口径，ADP-09）；设置读不出来就不查上限
            from genkit import settings as settings_mod

            image_adapter = settings_mod.load_settings_or_default()[0].role("image").adapter
            warnings = (check_duration_budget(spec) + validate_camera_usage(spec) + check_dialogue_clarity(spec)
                        + check_continuity_rules(spec, image_adapter=image_adapter))
            for line in warnings:
                print(f"⚠ {line}", file=sys.stderr)
        elif args.command == "preview":
            spec = load_spec(args.episode, project_root=args.project_root)
            markdown = render_preview(spec, shot_ids=_items(args.only, label="shot"))
            if args.stdout:
                print(markdown)
            else:
                out_path = Path(args.out) if args.out else (
                    Path(spec["output_root"]) / "manifests" / "preview.md"
                )
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(markdown, encoding="utf-8")
                print(f"wrote {out_path}")
            result = {
                "run_id": spec.get("id"),
                "status": "previewed (not locked)",
                "run_dir": spec["output_root"],
            }
        elif args.command == "import-legacy":
            with StoryVideoRunner(args.project_root) as runner:
                result = runner.import_legacy(
                    args.episode,
                    confirmed_existing_artifacts=args.confirm_existing_artifacts,
                )
        elif args.command == "run":
            provider = build_provider()
            with StoryVideoRunner(args.project_root, provider=provider) as runner:
                result = runner.run(
                    args.episode,
                    only=_items(args.only, label="shot"),
                    through=args.through,
                    wait=not args.no_wait,
                )
        elif args.command == "status":
            with StoryVideoRunner(args.project_root, readonly=True) as runner:
                result = runner.status(args.episode)
        elif args.command == "review":
            with StoryVideoRunner(args.project_root) as runner:
                result = runner.review(
                    args.episode,
                    args.gate,
                    args.decision,
                    shots=_items(args.shots, label="shot"),
                    reason=args.reason,
                    reviewer=args.reviewer,
                    evidence=args.evidence,
                )
        elif args.command == "asr-qc":
            from .asr_qc import failed_shot_ids, retake_reason, run_asr_qc

            spec = load_spec(args.episode, project_root=args.project_root)
            report = run_asr_qc(
                spec, shots=_items(args.only, label="shot"), threshold=args.threshold, model=args.model
            )
            for row in report["rows"]:
                mark = {"pass": "✓", "review": "?", "fail": "✗", "silent": "✗", "stray": "△", "no_lines": "·", "missing_video": "-"}[row["status"]]
                print(f"{mark} {row['shot_id']:<22} {row['status']:<13} cer={row['cer']}  期望「{row['expected']}」 听到「{row['heard']}」")
            print(f"summary: {report['summary']}")
            failing = failed_shot_ids(report, include_stray=args.include_stray)
            if args.record_retakes and failing:
                rows = {r["shot_id"]: r for r in report["rows"]}
                with StoryVideoRunner(args.project_root) as runner:
                    for shot_id in failing:
                        runner.review(
                            args.episode, "videos", "retake", shots=[shot_id],
                            reason=retake_reason(rows[shot_id]), reviewer="asr-qc",
                        )
                print(f"recorded retake for {len(failing)} shot(s): {', '.join(failing)}")
            result = {
                "run_id": spec.get("id"),
                "status": f"asr-qc pass={report['summary']['pass']} fail={report['summary']['fail']} "
                          f"review={report['summary'].get('review', 0)} stray={report['summary']['stray']} missing={report['summary']['missing_video']}",
                "summary": report["summary"], "failing": failing,
            }
        elif args.command == "retry-failed":
            with StoryVideoRunner(args.project_root) as runner:
                result = runner.retry_failed(args.episode, only=_items(args.only, label="shot"), reason=args.reason)
        elif args.command == "amend":
            with StoryVideoRunner(args.project_root) as runner:
                info = runner.amend_post_layers(args.episode)
            result = {"run_id": info["run_id"], "status": f"amend {info['status']}: {', '.join(info['changed_keys']) or '-'}"}
        elif args.command == "post":
            from .post_layers import compose_post

            spec = load_spec(args.episode, project_root=args.project_root)
            manifest = compose_post(spec, destination=args.destination)
            result = {
                "run_id": spec.get("id"),
                "status": f"post composed {manifest['duration_seconds']:.1f}s → {manifest['output']}",
                "timeline": manifest["timeline"], "vo": len(manifest["vo"]), "ui_inserts": manifest["ui_inserts"],
            }
        elif args.command == "reconcile":
            with StoryVideoRunner(
                args.project_root, provider=build_provider()
            ) as runner:
                result = runner.reconcile(args.episode, args.step_id, args.task_id)
        elif args.command == "retry":
            with StoryVideoRunner(args.project_root) as runner:
                result = runner.retry(
                    args.episode,
                    step_ids=_items(args.steps, label="step"),
                    reason=args.reason,
                )
        elif args.command == "abandon":
            with StoryVideoRunner(
                args.project_root, provider=build_provider()
            ) as runner:
                result = runner.abandon(
                    args.episode,
                    args.step_id,
                    reason=args.reason,
                    confirmed_not_submitted=args.confirm_no_remote_task,
                )
        else:  # pragma: no cover - argparse guarantees this
            raise StoryVideoError(f"unsupported command: {args.command}")
        _print(result, as_json=args.json)
        return 0
    except StoryVideoGatePending as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except StoryVideoBlocked as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (
        ProviderError,
        AssemblyError,
        MediaValidationError,
        QCError,
        RunCollisionError,
        SpecError,
        StoreError,
        StoryVideoError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

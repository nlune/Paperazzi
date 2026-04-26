from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def print_block(title: str, payload: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Paperazzi create -> analysis -> render_voice pipeline."
    )
    parser.add_argument("pdf_path", help="Path to the PDF to upload.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL.",
    )
    parser.add_argument(
        "--style",
        default="clean academic explainer",
        help="Project style brief.",
    )
    parser.add_argument(
        "--voice-profile",
        default="clear educational narrator",
        help="Project voice profile brief.",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=None,
        help="Limit analysis to the first N pages.",
    )
    parser.add_argument(
        "--section-limit",
        type=int,
        default=None,
        help="Limit planning to the first N includable sections.",
    )
    parser.add_argument(
        "--max-targets",
        type=int,
        default=3,
        help="Maximum number of visual targets per section.",
    )
    parser.add_argument(
        "--mock-planner",
        action="store_true",
        help="Use the local mock planner instead of Gemini.",
    )
    parser.add_argument(
        "--mock-voice",
        action="store_true",
        help="Use mock voice timing instead of Gradium.",
    )
    parser.add_argument(
        "--skip-voice",
        action="store_true",
        help="Stop after analysis instead of calling /render_voice.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between project status polls.",
    )
    return parser.parse_args()


def wait_for_stage(
    client: httpx.Client,
    base_url: str,
    project_id: str,
    expected_stage: str,
    poll_interval: float,
) -> dict:
    while True:
        response = client.get(f"{base_url}/projects/{project_id}", timeout=60.0)
        if response.status_code >= 400:
            raise SystemExit(response.text)
        payload = response.json()
        print(
            f"[{payload['current_stage']}] {payload['progress_percent']}% - "
            f"{payload['stage_label']}"
        )
        if payload["current_stage"] == expected_stage:
            return payload
        if payload["current_stage"] == "failed":
            raise SystemExit(payload.get("error_message") or "Project failed.")
        time.sleep(poll_interval)


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf_path).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"Missing PDF: {pdf_path}")

    base_url = args.base_url.rstrip("/")
    with httpx.Client() as client:
        with pdf_path.open("rb") as pdf_file:
            create_response = client.post(
                f"{base_url}/projects",
                data={
                    "style": args.style,
                    "voice_profile": args.voice_profile,
                },
                files={"file": (pdf_path.name, pdf_file, "application/pdf")},
                timeout=60.0,
            )
        if create_response.status_code >= 400:
            raise SystemExit(create_response.text)
        project = create_response.json()
        project_id = project["project_id"]
        print_block("Project Created", project)

        analysis_response = client.post(
            f"{base_url}/projects/{project_id}/analysis",
            json={
                "page_limit": args.page_limit,
                "section_limit": args.section_limit,
                "max_targets_per_section": args.max_targets,
                "use_mock_planner": args.mock_planner,
            },
            timeout=60.0,
        )
        if analysis_response.status_code >= 400:
            raise SystemExit(analysis_response.text)
        print_block("Analysis Started", analysis_response.json())

        project = wait_for_stage(
            client,
            base_url,
            project_id,
            expected_stage="analysis_ready",
            poll_interval=args.poll_interval,
        )
        analysis = client.get(f"{base_url}/projects/{project_id}/analysis", timeout=60.0)
        if analysis.status_code >= 400:
            raise SystemExit(analysis.text)
        analysis_payload = analysis.json()
        print_block(
            "Analysis Summary",
            {
                "project_id": project_id,
                "section_count": len(analysis_payload.get("sections", [])),
                "narrated_unit_count": len(analysis_payload.get("narrated_units", [])),
                "visual_target_count": len(analysis_payload.get("visual_targets", [])),
                "section_word_count": len(analysis_payload.get("section_words", [])),
                "highlight_word_count": len(analysis_payload.get("highlight_words", [])),
                "narration_word_count": len(analysis_payload.get("narration_words", [])),
                "animation_beat_count": len(analysis_payload.get("animation_beats", [])),
                "transition_count": len(analysis_payload.get("transitions", [])),
                "action_count": len(analysis_payload.get("action_templates", [])),
                "warnings": analysis_payload.get("warnings", []),
                "unresolved": analysis_payload.get("unresolved", []),
            },
        )

        if args.skip_voice:
            return

        voice_response = client.post(
            f"{base_url}/projects/{project_id}/render_voice",
            json={"use_mock_voice": args.mock_voice},
            timeout=60.0,
        )
        if voice_response.status_code >= 400:
            raise SystemExit(voice_response.text)
        print_block("Render Voice Started", voice_response.json())

        project = wait_for_stage(
            client,
            base_url,
            project_id,
            expected_stage="voice_ready",
            poll_interval=args.poll_interval,
        )
        voice = client.get(f"{base_url}/projects/{project_id}/voice", timeout=60.0)
        if voice.status_code >= 400:
            raise SystemExit(voice.text)
        voice_payload = voice.json()
        print_block(
            "Voice Summary",
            {
                "mode": voice_payload.get("mode"),
                "duration_s": voice_payload.get("duration_s"),
                "segment_count": len(voice_payload.get("text_segments", [])),
                "word_timing_count": len(voice_payload.get("narration_word_timings", [])),
                "action_timing_count": len(voice_payload.get("action_timings", [])),
                "timed_animation_beat_count": len(
                    voice_payload.get("timed_animation_beats", [])
                ),
                "timed_transition_count": len(voice_payload.get("timed_transitions", [])),
                "warnings": voice_payload.get("warnings", []),
            },
        )

        print_block(
            "Artifacts",
            {
                "analysis_path": project["analysis"]["analysis_path"]
                if project.get("analysis")
                else None,
                "voice_path": project["voice"]["voice_path"] if project.get("voice") else None,
            },
        )


if __name__ == "__main__":
    main()

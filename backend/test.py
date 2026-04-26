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
        description="Run the Paperazzi PDF -> concepts -> storyboards flow."
    )
    parser.add_argument("pdf_path", help="Path to the PDF to upload.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL.",
    )
    parser.add_argument(
        "--style",
        default="Bold educational animation with warm cinematic lighting",
        help="Global style setting for the project.",
    )
    parser.add_argument(
        "--voice-profile",
        default="Warm, authoritative explainer narrator",
        help="Global voice profile for the project.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between project status polls.",
    )
    return parser.parse_args()


def wait_for_terminal_stage(
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

    if not pdf_path.exists() or not pdf_path.is_file():
        raise SystemExit(f"PDF path does not exist or is not a file: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise SystemExit(f"File must have a .pdf extension: {pdf_path}")

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

        concepts_response = client.post(
            f"{base_url}/projects/{project_id}/concepts",
            timeout=60.0,
        )
        if concepts_response.status_code >= 400:
            raise SystemExit(concepts_response.text)
        print_block("Concept Extraction Started", concepts_response.json())

        project = wait_for_terminal_stage(
            client=client,
            base_url=base_url,
            project_id=project_id,
            expected_stage="concepts_ready",
            poll_interval=args.poll_interval,
        )
        print_block("Concepts", project["concepts"])

        storyboards_response = client.post(
            f"{base_url}/projects/{project_id}/storyboards",
            timeout=60.0,
        )
        if storyboards_response.status_code >= 400:
            raise SystemExit(storyboards_response.text)
        print_block("Storyboard Generation Started", storyboards_response.json())

        project = wait_for_terminal_stage(
            client=client,
            base_url=base_url,
            project_id=project_id,
            expected_stage="storyboards_ready",
            poll_interval=args.poll_interval,
        )
        print_block("Storyboards", project["storyboards"])

        print_block(
            "Final Project State",
            {
                "project_id": project["project_id"],
                "current_stage": project["current_stage"],
                "progress_percent": project["progress_percent"],
                "stage_label": project["stage_label"],
                "concept_count": len(project["concepts"]),
                "storyboard_count": len(project["storyboards"]),
            },
        )


if __name__ == "__main__":
    main()

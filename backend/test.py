import argparse
import json
from pathlib import Path

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a PDF to the Paperazzi backend and print the concepts."
    )
    parser.add_argument("pdf_path", help="Path to the PDF file to upload.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/concepts",
        help="FastAPI endpoint URL.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf_path).expanduser().resolve()

    if not pdf_path.exists() or not pdf_path.is_file():
        raise SystemExit(f"PDF path does not exist or is not a file: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise SystemExit(f"File must have a .pdf extension: {pdf_path}")

    with pdf_path.open("rb") as pdf_file:
        response = httpx.post(
            args.url,
            files={"file": (pdf_path.name, pdf_file, "application/pdf")},
            timeout=120,
        )

    print(f"Status: {response.status_code}")

    try:
        payload = response.json()
    except json.JSONDecodeError:
        print(response.text)
        return

    print(json.dumps(payload, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()

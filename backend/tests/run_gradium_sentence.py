from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from gradium.client import GradiumClient
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    GradiumClient = None  # type: ignore[assignment]


DEFAULT_SENTENCE = (
    "Because compression happened gradually, his brain adapted and remapped "
    "functions to the thin remaining layer of tissue. "
    "The remaining 10% assumed functions for the entire brain."
)
DEFAULT_WORDS = ("compression", "brain", "layer", "functions")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a direct Gradium sentence synthesis and save timestamps."
    )
    parser.add_argument(
        "--sentence",
        default=DEFAULT_SENTENCE,
        help="Sentence to synthesize.",
    )
    parser.add_argument(
        "--words",
        default=",".join(DEFAULT_WORDS),
        help="Comma-separated words of interest.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "test_outputs"),
        help="Directory to store audio and timestamp outputs.",
    )
    return parser.parse_args()


def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", text.lower())


def _word_timings_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    word_timings: list[dict[str, Any]] = []
    occurrence_by_word: dict[str, int] = {}

    for segment in segments:
        segment_text = str(segment.get("text", ""))
        start_s = float(segment.get("start_s", 0.0))
        stop_s = float(segment.get("stop_s", start_s))
        words = _tokenize_words(segment_text)
        if not words:
            continue

        duration = max(0.001, stop_s - start_s)
        word_duration = duration / len(words)
        for idx, word in enumerate(words):
            word_start = start_s + idx * word_duration
            word_stop = stop_s if idx == len(words) - 1 else word_start + word_duration
            occurrence_by_word[word] = occurrence_by_word.get(word, 0) + 1
            word_timings.append(
                {
                    "word": word,
                    "occurrence": occurrence_by_word[word],
                    "start_s": word_start,
                    "stop_s": word_stop,
                }
            )

    return word_timings


async def _synthesize_with_gradium(
    sentence: str,
    *,
    base_url: str,
    voice_id: str | None,
) -> tuple[bytes, list[dict[str, Any]]]:
    if GradiumClient is None:
        raise SystemExit("gradium package is not installed in this Python environment.")
    client = GradiumClient(base_url=base_url)
    audio_chunks: list[bytes] = []
    segments: list[dict[str, Any]] = []

    async with client.tts_realtime(
        model_name="default",
        voice_id=voice_id,
        output_format="wav",
        wait_for_ready_on_start=True,
    ) as tts:
        await tts.send_text(sentence, client_req_id="gradium-script-sentence")
        await tts.send_eos()

        async for message in tts:
            msg_type = message.get("type")
            if msg_type == "audio":
                audio_chunks.append(message["audio"])
            elif msg_type == "text":
                segments.append(
                    {
                        "text": message.get("text", ""),
                        "start_s": float(message.get("start_s", 0.0)),
                        "stop_s": float(
                            message.get("stop_s", message.get("start_s", 0.0))
                        ),
                    }
                )

    return b"".join(audio_chunks), segments


def main() -> None:
    args = parse_args()

    if "GRADIUM_API_KEY" not in os.environ:
        raise SystemExit("GRADIUM_API_KEY is required.")

    words_of_interest = {
        token.strip().lower()
        for token in args.words.split(",")
        if token.strip()
    }
    if not words_of_interest:
        raise SystemExit("At least one word of interest is required.")

    base_url = os.getenv("GRADIUM_BASE_URL", "https://eu.api.gradium.ai/api/")
    voice_id = os.getenv("GRADIUM_VOICE_ID")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_bytes, segments = asyncio.run(
        _synthesize_with_gradium(args.sentence, base_url=base_url, voice_id=voice_id)
    )
    if not audio_bytes:
        raise SystemExit("Gradium returned empty audio bytes.")
    if not segments:
        raise SystemExit("Gradium returned no text timing segments.")

    audio_path = output_dir / "gradium_sentence.wav"
    audio_path.write_bytes(audio_bytes)

    all_word_timings = _word_timings_from_segments(segments)
    selected_timings = [
        item for item in all_word_timings if item["word"] in words_of_interest
    ]

    found_words = {item["word"] for item in selected_timings}
    missing_words = sorted(words_of_interest - found_words)
    if missing_words:
        raise SystemExit(
            f"Missing words of interest in timing output: {', '.join(missing_words)}"
        )

    timings_path = output_dir / "gradium_sentence_interest_word_timestamps.json"
    timings_payload = {
        "sentence": args.sentence,
        "words_of_interest": sorted(words_of_interest),
        "segments": segments,
        "interest_word_timestamps": selected_timings,
        "audio_path": str(audio_path.resolve()),
    }
    timings_path.write_text(
        json.dumps(timings_payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )

    print(f"Saved audio: {audio_path}")
    print(f"Saved timestamps: {timings_path}")


if __name__ == "__main__":
    main()

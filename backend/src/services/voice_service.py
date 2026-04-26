from __future__ import annotations

import asyncio
import wave
from datetime import datetime, timezone
from pathlib import Path

from gradium.client import GradiumClient

from src.config import get_settings
from src.models import (
    ActionTemplate,
    AnimationBeat,
    AnalysisRecord,
    NarrationWord,
    RenderVoiceRequest,
    TimedAction,
    TimedAnimationBeat,
    TimedNarrationWord,
    TimedText,
    TimedTransition,
    TransitionPlan,
    VoiceRenderRecord,
    VoiceSummary,
)
from src.services.text_tokens import tokenize_words
from src.storage import load_project, mutate_project, project_dir, write_json


def _set_failure(project_id: str, stage_label: str, exc: Exception) -> None:
    mutate_project(
        project_id,
        lambda project: (
            setattr(project, "current_stage", "failed"),
            setattr(project, "progress_percent", 100),
            setattr(project, "stage_label", stage_label),
            setattr(project, "error_message", str(exc)),
        ),
    )


def _load_analysis(project_id: str) -> AnalysisRecord:
    project = load_project(project_id)
    if project.analysis is None:
        raise RuntimeError("Analysis must be ready before render_voice.")
    return AnalysisRecord.model_validate_json(
        Path(project.analysis.analysis_path).read_text(encoding="utf-8")
    )


def _write_silent_wav(path: Path, duration_s: float, sample_rate: int = 24000) -> None:
    frame_count = max(1, int(duration_s * sample_rate))
    silence = b"\x00\x00" * frame_count
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(silence)


def _timed_words_evenly(
    narration_words: list[NarrationWord],
    start_s: float,
    stop_s: float,
) -> list[TimedNarrationWord]:
    if not narration_words:
        return []
    word_duration = max(0.001, (stop_s - start_s) / len(narration_words))
    timed_words: list[TimedNarrationWord] = []
    for index, word in enumerate(narration_words):
        word_start = start_s + index * word_duration
        word_stop = stop_s if index == len(narration_words) - 1 else word_start + word_duration
        timed_words.append(
            TimedNarrationWord(
                narration_word_id=word.narration_word_id,
                unit_id=word.unit_id,
                action_id=word.action_id,
                visual_target_id=word.visual_target_id,
                word=word.word,
                normalized_word=word.normalized_word,
                occurrence=word.occurrence,
                start_s=word_start,
                stop_s=word_stop,
                highlight_word_ids=word.highlight_word_ids,
            )
        )
    return timed_words


def _timed_words_from_segments(
    *,
    action: ActionTemplate,
    narration_words: list[NarrationWord],
    segments: list[TimedText],
    fallback_start_s: float,
    fallback_stop_s: float,
) -> tuple[list[TimedNarrationWord], list[str]]:
    warnings: list[str] = []
    if not narration_words:
        return [], warnings
    if not segments:
        warnings.append(
            f"No Gradium text timing segments for {action.action_id}; interpolated word timings."
        )
        return (
            _timed_words_evenly(narration_words, fallback_start_s, fallback_stop_s),
            warnings,
        )

    exact_word_segments = [
        segment for segment in segments if len(tokenize_words(segment.text)) == 1
    ]
    if len(exact_word_segments) == len(narration_words):
        timed_words = []
        for word, segment in zip(narration_words, exact_word_segments, strict=False):
            timed_words.append(
                TimedNarrationWord(
                    narration_word_id=word.narration_word_id,
                    unit_id=word.unit_id,
                    action_id=word.action_id,
                    visual_target_id=word.visual_target_id,
                    word=word.word,
                    normalized_word=word.normalized_word,
                    occurrence=word.occurrence,
                    start_s=segment.start_s,
                    stop_s=segment.stop_s,
                    highlight_word_ids=word.highlight_word_ids,
                )
            )
        return timed_words, warnings

    timed_words: list[TimedNarrationWord] = []
    next_word_index = 0
    for segment in segments:
        segment_tokens = tokenize_words(segment.text)
        if not segment_tokens:
            continue
        word_count = min(len(segment_tokens), len(narration_words) - next_word_index)
        if word_count <= 0:
            break
        timed_words.extend(
            _timed_words_evenly(
                narration_words[next_word_index : next_word_index + word_count],
                segment.start_s,
                segment.stop_s,
            )
        )
        next_word_index += word_count

    if len(timed_words) < len(narration_words):
        remaining = narration_words[len(timed_words) :]
        start_s = timed_words[-1].stop_s if timed_words else fallback_start_s
        timed_words.extend(_timed_words_evenly(remaining, start_s, fallback_stop_s))
    warnings.append(
        f"Gradium did not return one text segment per word for {action.action_id}; interpolated word timings within returned segments."
    )
    return timed_words, warnings


def _timed_animation_beats(
    beats: list[AnimationBeat],
    timed_words: list[TimedNarrationWord],
) -> list[TimedAnimationBeat]:
    timings_by_word = {word.narration_word_id: word for word in timed_words}
    timed_beats: list[TimedAnimationBeat] = []
    for beat in beats:
        timing = timings_by_word.get(beat.narration_word_id)
        if timing is None:
            continue
        timed_beats.append(
            TimedAnimationBeat(
                beat_id=beat.beat_id,
                unit_id=beat.unit_id,
                action_id=beat.action_id,
                visual_target_id=beat.visual_target_id,
                primitive=beat.primitive,
                narration_word_id=beat.narration_word_id,
                start_s=timing.start_s,
                stop_s=timing.stop_s,
                highlight_word_ids=beat.highlight_word_ids,
                action_hint=beat.action_hint,
            )
        )
    return timed_beats


def _timed_transitions(
    transitions: list[TransitionPlan],
    action_timings: list[TimedAction],
    actions_by_id: dict[str, ActionTemplate],
) -> tuple[list[TimedTransition], list[str]]:
    warnings: list[str] = []
    timings_by_unit: dict[str, list[TimedAction]] = {}
    for timing in action_timings:
        action = actions_by_id.get(timing.action_id)
        unit_id = timing.unit_id or (action.unit_id if action else None)
        if unit_id is None:
            continue
        timings_by_unit.setdefault(unit_id, []).append(timing)

    timed: list[TimedTransition] = []
    for transition in transitions:
        to_timings = timings_by_unit.get(transition.to_unit_id, [])
        if not to_timings:
            continue
        to_start = min(timing.start_s for timing in to_timings)
        from_stop = 0.0
        if transition.from_unit_id:
            from_timings = timings_by_unit.get(transition.from_unit_id, [])
            from_stop = max((timing.stop_s for timing in from_timings), default=0.0)
        stop_s = to_start
        start_s = max(0.0, stop_s - transition.duration_s)
        if from_stop > start_s:
            warnings.append(
                f"{transition.transition_id} overlaps narration because there is not enough silent gap before {transition.to_unit_id}."
            )
        timed.append(
            TimedTransition(
                transition_id=transition.transition_id,
                transition_type=transition.transition_type,
                from_unit_id=transition.from_unit_id,
                to_unit_id=transition.to_unit_id,
                from_page=transition.from_page,
                to_page=transition.to_page,
                target_section_id=transition.target_section_id,
                target_bbox=transition.target_bbox,
                start_s=start_s,
                stop_s=stop_s,
            )
        )
    return timed, warnings


def _mock_voice(
    analysis: AnalysisRecord,
    pause_between_sections_s: float,
) -> tuple[bytes, list[TimedText], list[TimedAction], list[TimedNarrationWord], float]:
    current = 0.0
    segments: list[TimedText] = []
    action_timings: list[TimedAction] = []
    word_timings: list[TimedNarrationWord] = []
    previous_unit_id: str | None = None
    words_by_action: dict[str, list[NarrationWord]] = {}
    for word in analysis.narration_words:
        words_by_action.setdefault(word.action_id, []).append(word)

    for action in analysis.action_templates:
        if previous_unit_id is not None and action.unit_id != previous_unit_id:
            current += pause_between_sections_s
        duration = max(1.0, len(action.spoken_text.split()) / 2.6)
        start_s = current
        stop_s = current + duration
        segments.append(
            TimedText(
                text=action.spoken_text,
                start_s=start_s,
                stop_s=stop_s,
                client_req_id=action.action_id,
            )
        )
        action_timings.append(
            TimedAction(
                action_id=action.action_id,
                start_s=start_s,
                stop_s=stop_s,
                spoken_text=action.spoken_text,
                primitive=action.primitive,
                visual_target_id=action.visual_target_id,
                unit_id=action.unit_id,
                highlight_word_ids=action.highlight_word_ids,
                narration_word_ids=action.narration_word_ids,
            )
        )
        word_timings.extend(
            _timed_words_evenly(words_by_action.get(action.action_id, []), start_s, stop_s)
        )
        current = stop_s
        previous_unit_id = action.unit_id

    return b"", segments, action_timings, word_timings, current


async def _gradium_voice_async(
    *,
    analysis: AnalysisRecord,
    voice_id: str | None,
    base_url: str,
) -> tuple[bytes, list[TimedText], list[TimedAction], list[TimedNarrationWord], float, list[str]]:
    actions = analysis.action_templates
    client = GradiumClient(base_url=base_url)
    chunks: list[bytes] = []
    text_segments: list[TimedText] = []
    by_action: dict[str, list[TimedText]] = {action.action_id: [] for action in actions}
    words_by_action: dict[str, list[NarrationWord]] = {}
    for word in analysis.narration_words:
        words_by_action.setdefault(word.action_id, []).append(word)

    async with client.tts_realtime(
        model_name="default",
        voice_id=voice_id,
        output_format="wav",
        wait_for_ready_on_start=True,
    ) as tts:
        for action in actions:
            await tts.send_text(action.spoken_text, client_req_id=action.action_id)
        await tts.send_eos()

        async for message in tts:
            msg_type = message.get("type")
            if msg_type == "audio":
                chunks.append(message["audio"])
            elif msg_type == "text":
                segment = TimedText(
                    text=message.get("text", ""),
                    start_s=float(message.get("start_s", 0.0)),
                    stop_s=float(message.get("stop_s", message.get("start_s", 0.0))),
                    client_req_id=message.get("client_req_id"),
                )
                text_segments.append(segment)
                if segment.client_req_id in by_action:
                    by_action[segment.client_req_id].append(segment)

    action_timings: list[TimedAction] = []
    word_timings: list[TimedNarrationWord] = []
    warnings: list[str] = []
    max_stop = 0.0
    for action in actions:
        segments = by_action.get(action.action_id, [])
        if segments:
            start_s = min(segment.start_s for segment in segments)
            stop_s = max(segment.stop_s for segment in segments)
        else:
            start_s = max_stop
            stop_s = max_stop + max(1.0, len(action.spoken_text.split()) / 2.6)
        max_stop = max(max_stop, stop_s)
        action_timings.append(
            TimedAction(
                action_id=action.action_id,
                unit_id=action.unit_id,
                start_s=start_s,
                stop_s=stop_s,
                spoken_text=action.spoken_text,
                primitive=action.primitive,
                visual_target_id=action.visual_target_id,
                highlight_word_ids=action.highlight_word_ids,
                narration_word_ids=action.narration_word_ids,
            )
        )
        action_words, action_warnings = _timed_words_from_segments(
            action=action,
            narration_words=words_by_action.get(action.action_id, []),
            segments=segments,
            fallback_start_s=start_s,
            fallback_stop_s=stop_s,
        )
        word_timings.extend(action_words)
        warnings.extend(action_warnings)

    duration = max((segment.stop_s for segment in text_segments), default=max_stop)
    return b"".join(chunks), text_segments, action_timings, word_timings, duration, warnings


def render_voice(project_id: str, request: RenderVoiceRequest) -> None:
    try:
        mutate_project(
            project_id,
            lambda project: (
                setattr(project, "current_stage", "generating_voice"),
                setattr(project, "progress_percent", 10),
                setattr(project, "stage_label", "Loading analysis"),
                setattr(project, "error_message", None),
                setattr(project, "voice", None),
            ),
        )

        settings = get_settings()
        analysis = _load_analysis(project_id)
        actions = analysis.action_templates
        if not actions:
            raise RuntimeError("Analysis contains no action templates to voice.")
        actions_by_id = {action.action_id: action for action in actions}

        voice_id = request.voice_id or settings.gradium_voice_id
        use_mock_voice = request.use_mock_voice
        warnings: list[str] = []

        if not use_mock_voice and "GRADIUM_API_KEY" not in __import__("os").environ:
            if settings.allow_mock_services:
                use_mock_voice = True
                warnings.append(
                    "Using mock voice because GRADIUM_API_KEY is not configured."
                )
            else:
                raise RuntimeError("GRADIUM_API_KEY is required for render_voice.")

        mutate_project(
            project_id,
            lambda project: (
                setattr(project, "progress_percent", 40),
                setattr(project, "stage_label", "Synthesizing narration"),
            ),
        )

        if use_mock_voice:
            _, text_segments, action_timings, word_timings, duration_s = _mock_voice(
                analysis, pause_between_sections_s=request.pause_between_sections_s
            )
            mode = "mock"
            audio_bytes = b""
        else:
            if request.pause_between_sections_s:
                warnings.append(
                    "Gradium voice synthesis does not currently insert explicit silent gaps between section requests."
                )
            (
                audio_bytes,
                text_segments,
                action_timings,
                word_timings,
                duration_s,
                gradium_warnings,
            ) = asyncio.run(
                _gradium_voice_async(
                    analysis=analysis,
                    voice_id=voice_id,
                    base_url=settings.gradium_base_url,
                )
            )
            warnings.extend(gradium_warnings)
            mode = "gradium"

        timed_beats = _timed_animation_beats(analysis.animation_beats, word_timings)
        timed_transitions, transition_warnings = _timed_transitions(
            analysis.transitions,
            action_timings,
            actions_by_id,
        )
        warnings.extend(transition_warnings)

        artifact_dir = project_dir(project_id)
        audio_path = artifact_dir / "narration.wav"
        if mode == "mock":
            _write_silent_wav(audio_path, duration_s)
        else:
            audio_path.write_bytes(audio_bytes)

        narration_script_path = artifact_dir / "narration_script.txt"
        narration_script_path.write_text(
            "\n".join(f"[{action.action_id}] {action.spoken_text}" for action in actions),
            encoding="utf-8",
        )

        caption_timeline_path = artifact_dir / "caption_timeline.json"
        timed_actions_path = artifact_dir / "timed_actions.json"
        narration_word_timings_path = artifact_dir / "narration_word_timings.json"
        timed_animation_beats_path = artifact_dir / "timed_animation_beats.json"
        timed_transitions_path = artifact_dir / "timed_transitions.json"
        voice_render_path = artifact_dir / "voice_render.json"

        write_json(
            caption_timeline_path,
            {"segments": [segment.model_dump(mode="json") for segment in text_segments]},
        )
        write_json(
            timed_actions_path,
            {"actions": [action.model_dump(mode="json") for action in action_timings]},
        )
        write_json(
            narration_word_timings_path,
            {"words": [word.model_dump(mode="json") for word in word_timings]},
        )
        write_json(
            timed_animation_beats_path,
            {"beats": [beat.model_dump(mode="json") for beat in timed_beats]},
        )
        write_json(
            timed_transitions_path,
            {
                "transitions": [
                    transition.model_dump(mode="json") for transition in timed_transitions
                ]
            },
        )

        voice_render = VoiceRenderRecord(
            project_id=project_id,
            created_at=datetime.now(timezone.utc),
            mode=mode,  # type: ignore[arg-type]
            voice_id=voice_id,
            audio_path=str(audio_path.resolve()),
            duration_s=duration_s,
            text_segments=text_segments,
            narration_word_timings=word_timings,
            action_timings=action_timings,
            timed_animation_beats=timed_beats,
            timed_transitions=timed_transitions,
            caption_timeline=text_segments,
            warnings=warnings,
        )
        write_json(voice_render_path, voice_render.model_dump(mode="json"))

        summary = VoiceSummary(
            voice_path=str(audio_path.resolve()),
            timeline_path=str(timed_actions_path.resolve()),
            caption_timeline_path=str(caption_timeline_path.resolve()),
            segment_count=len(text_segments),
            word_timing_count=len(word_timings),
            action_timing_count=len(action_timings),
            duration_s=duration_s,
            mode=mode,  # type: ignore[arg-type]
            voice_id=voice_id,
        )

        mutate_project(
            project_id,
            lambda project: (
                setattr(project, "current_stage", "voice_ready"),
                setattr(project, "progress_percent", 100),
                setattr(project, "stage_label", "Voice timing ready"),
                setattr(project, "voice", summary),
                setattr(project, "warnings", list(dict.fromkeys(project.warnings + warnings))),
                setattr(project, "error_message", None),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _set_failure(project_id, "Voice generation failed", exc)

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.config import get_settings
from src.models import (
    ActionTemplate,
    AnimationBeat,
    AnalysisRecord,
    AnalysisRequest,
    AnalysisSummary,
    HighlightWord,
    NarrationWord,
    NarratedUnit,
    PageBBox,
    Primitive,
    ProjectRecord,
    SectionDecision,
    SectionRecord,
    SectionWordRef,
    TransitionPlan,
    VisualTarget,
    WordIndexPage,
)
from src.services.docling_service import (
    build_docling_payload,
    build_sections,
    convert_pdf_with_docling,
)
from src.services.pdf_service import (
    build_word_index,
    get_pdf_page_count,
    render_pages,
    resolve_visual_target,
    section_word_refs,
    truncate_pdf,
    word_to_page_bbox,
)
from src.services.planner import ALLOWED_PRIMITIVES, PlannerError, get_section_planner
from src.services.text_tokens import normalize_token, occurrence_numbers, tokenize_words
from src.storage import load_project, mutate_project, project_dir, write_json


def _append_warning(project: ProjectRecord, warning: str) -> None:
    if warning not in project.warnings:
        project.warnings.append(warning)


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


def _mark_section_limit(sections: list[SectionRecord], section_limit: int | None) -> tuple[list[SectionRecord], list[str]]:
    if section_limit is None:
        return sections, []

    warnings: list[str] = []
    included_count = 0
    for section in sections:
        if not section.included:
            continue
        included_count += 1
        if included_count > section_limit:
            section.included = False
            section.skip_reason = "section_limit"
    if included_count > section_limit:
        warnings.append(
            f"Section limit {section_limit} applied; only the first {section_limit} includable sections were planned."
        )
    return sections, warnings


def _default_page_bbox(section: SectionRecord, pages_by_number: dict[int, dict]) -> PageBBox:
    if section.page_bboxes:
        return section.page_bboxes[0]
    page = pages_by_number[section.page_start]
    return PageBBox.model_validate(
        {
            "page": section.page_start,
            "bbox": {"x0": 0.0, "y0": 0.0, "x1": page["width"], "y1": page["height"]},
            "bbox_norm": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        }
    )


def _validate_primitive(value: str) -> Primitive:
    if value not in ALLOWED_PRIMITIVES:
        return "page_zoom_pan"
    return value  # type: ignore[return-value]


def _truncate_target_count(targets: list[dict], max_targets: int) -> list[dict]:
    return list(targets[:max_targets])


def _split_multi_page_sections(
    sections: list[SectionRecord],
) -> tuple[list[SectionRecord], list[str]]:
    split_sections: list[SectionRecord] = []
    warnings: list[str] = []

    for section in sections:
        if (
            not section.included
            or section.page_start == section.page_end
            or len(section.page_bboxes) <= 1
        ):
            split_sections.append(section)
            continue

        warnings.append(
            f"Split multi-page Docling section {section.section_id} into per-page planning sections."
        )
        for page_bbox in section.page_bboxes:
            page_items = [
                item for item in section.section_items if item.page_hint == page_bbox.page
            ]
            page_text = "\n\n".join(
                item.text for item in page_items if item.text.strip()
            ).strip()
            if not page_text:
                page_text = section.text_excerpt
            page_docling_refs = [item.item_id for item in page_items] or section.docling_refs
            split_sections.append(
                section.model_copy(
                    deep=True,
                    update={
                        "section_id": f"{section.section_id}-p{page_bbox.page}",
                        "page_start": page_bbox.page,
                        "page_end": page_bbox.page,
                        "docling_refs": page_docling_refs,
                        "page_bboxes": [page_bbox],
                        "text_excerpt": page_text[:237].rstrip() + "..."
                        if len(page_text) > 240
                        else page_text,
                        "section_text": page_text,
                        "char_count": len(page_text),
                        "section_items": page_items or section.section_items,
                    },
                )
            )

    for order, section in enumerate(split_sections, start=1):
        section.order = order
    return split_sections, warnings


def _collect_section_words(
    sections: list[SectionRecord],
    word_index: list[WordIndexPage],
) -> tuple[list[SectionWordRef], dict[tuple[str, int, int], str]]:
    refs: list[SectionWordRef] = []
    lookup: dict[tuple[str, int, int], str] = {}
    for section in sections:
        for ref in section_word_refs(section, word_index):
            refs.append(ref)
            lookup[(section.section_id, ref.page, ref.word_index)] = ref.word_ref_id
    return refs, lookup


def _highlight_words_for_target(
    *,
    target: VisualTarget,
    unit_id: str,
    section_id: str,
    word_index: list[WordIndexPage],
    section_word_lookup: dict[tuple[str, int, int], str],
    start_order: int,
) -> list[HighlightWord]:
    page = next((candidate for candidate in word_index if candidate.page == target.page), None)
    highlights: list[HighlightWord] = []
    occurrence_counts: dict[str, int] = {}

    if page is not None and target.word_refs:
        for ref in target.word_refs:
            if ref < 0 or ref >= len(page.words):
                continue
            word = page.words[ref]
            normalized = normalize_token(word.text)
            occurrence_counts[normalized] = occurrence_counts.get(normalized, 0) + 1
            word_bbox = word_to_page_bbox(page, word)
            highlights.append(
                HighlightWord(
                    highlight_id=f"highlight-{start_order + len(highlights):05d}",
                    unit_id=unit_id,
                    visual_target_id=target.target_id,
                    order=start_order + len(highlights),
                    source_word=word.text,
                    normalized_source_word=normalized,
                    source_occurrence=occurrence_counts[normalized],
                    page=target.page,
                    word_index=word.index,
                    section_word_ref_id=section_word_lookup.get(
                        (section_id, target.page, word.index)
                    ),
                    bbox=word_bbox.bbox,
                    bbox_norm=word_bbox.bbox_norm,
                )
            )

    if highlights:
        return highlights

    normalized = normalize_token(target.anchor_text or target.label)
    return [
        HighlightWord(
            highlight_id=f"highlight-{start_order:05d}",
            unit_id=unit_id,
            visual_target_id=target.target_id,
            order=start_order,
            source_word=target.anchor_text or target.label,
            normalized_source_word=normalized,
            source_occurrence=1,
            page=target.page,
            word_index=None,
            section_word_ref_id=None,
            bbox=target.union_bbox,
            bbox_norm=target.union_bbox_norm,
        )
    ]


def _build_narration_words_and_beats(
    *,
    action: ActionTemplate,
    target_highlights: list[HighlightWord],
    next_word_order: int,
    next_beat_order: int,
) -> tuple[list[NarrationWord], list[AnimationBeat]]:
    tokens = tokenize_words(action.spoken_text)
    occurrences = occurrence_numbers(tokens)
    highlight_by_occurrence: dict[tuple[str, int], str] = {}
    highlight_by_norm: dict[str, list[str]] = {}
    for highlight in target_highlights:
        if not highlight.normalized_source_word:
            continue
        highlight_by_occurrence[
            (highlight.normalized_source_word, highlight.source_occurrence)
        ] = highlight.highlight_id
        highlight_by_norm.setdefault(highlight.normalized_source_word, []).append(
            highlight.highlight_id
        )

    narration_words: list[NarrationWord] = []
    beats: list[AnimationBeat] = []
    for action_word_index, (token, occurrence) in enumerate(
        zip(tokens, occurrences, strict=False),
        start=1,
    ):
        normalized = normalize_token(token)
        highlight_id = highlight_by_occurrence.get((normalized, occurrence))
        if highlight_id is None:
            candidates = highlight_by_norm.get(normalized, [])
            highlight_id = candidates[min(occurrence - 1, len(candidates) - 1)] if candidates else None
        matched_highlights = [highlight_id] if highlight_id else []
        narration_word = NarrationWord(
            narration_word_id=f"nword-{next_word_order + len(narration_words):05d}",
            unit_id=action.unit_id,
            action_id=action.action_id,
            visual_target_id=action.visual_target_id,
            order=next_word_order + len(narration_words),
            action_word_index=action_word_index,
            word=token,
            normalized_word=normalized,
            occurrence=occurrence,
            highlight_word_ids=matched_highlights,
        )
        narration_words.append(narration_word)
        beats.append(
            AnimationBeat(
                beat_id=f"beat-{next_beat_order + len(beats):05d}",
                unit_id=action.unit_id,
                action_id=action.action_id,
                visual_target_id=action.visual_target_id,
                primitive=action.primitive,
                order=next_beat_order + len(beats),
                narration_word_id=narration_word.narration_word_id,
                narration_word=token,
                normalized_narration_word=normalized,
                highlight_word_ids=matched_highlights,
                action_hint="highlight_word" if matched_highlights else "hold_target",
            )
        )

    return narration_words, beats


def _build_transitions(units: list[NarratedUnit]) -> list[TransitionPlan]:
    transitions: list[TransitionPlan] = []
    previous: NarratedUnit | None = None
    for order, unit in enumerate(units, start=1):
        if previous is None:
            previous = unit
            continue
        transition_type = (
            "page_transition"
            if previous.primary_page != unit.primary_page
            else "section_scroll"
        )
        transitions.append(
            TransitionPlan(
                transition_id=f"transition-{order - 1:03d}",
                order=order - 1,
                transition_type=transition_type,  # type: ignore[arg-type]
                from_unit_id=previous.unit_id,
                to_unit_id=unit.unit_id,
                from_page=previous.primary_page,
                to_page=unit.primary_page,
                target_section_id=unit.source_section_ids[0],
                target_bbox=unit.focus_bbox,
                duration_s=0.75 if transition_type == "page_transition" else 0.55,
            )
        )
        previous = unit
    return transitions


def analyze_project(project_id: str, request: AnalysisRequest) -> None:
    temp_pdf_path: Path | None = None
    delete_temp_pdf = False
    try:
        mutate_project(
            project_id,
            lambda project: (
                setattr(project, "current_stage", "extracting_document"),
                setattr(project, "progress_percent", 5),
                setattr(project, "stage_label", "Preparing PDF"),
                setattr(project, "error_message", None),
                setattr(project, "analysis", None),
                setattr(project, "voice", None),
            ),
        )

        settings = get_settings()
        project = load_project(project_id)
        source_pdf = Path(project.pdf_storage_path)
        page_limit = request.page_limit
        max_targets = request.max_targets_per_section or settings.default_max_targets
        temp_pdf_path, delete_temp_pdf = truncate_pdf(source_pdf, page_limit)

        mutate_project(
            project_id,
            lambda current: (
                setattr(current, "progress_percent", 20),
                setattr(current, "stage_label", "Rendering PDF pages and word index"),
            ),
        )

        artifact_dir = project_dir(project_id)
        pages = render_pages(
            temp_pdf_path, artifact_dir / "pages", settings.default_page_image_dpi
        )
        pages_by_number = {page.page: page.model_dump() for page in pages}
        word_index = build_word_index(temp_pdf_path)
        word_index_path = artifact_dir / "word_index.json"
        write_json(
            word_index_path,
            {"pages": [page.model_dump(mode="json") for page in word_index]},
        )

        mutate_project(
            project_id,
            lambda current: (
                setattr(current, "progress_percent", 40),
                setattr(current, "stage_label", "Extracting sections with Docling"),
            ),
        )

        document = convert_pdf_with_docling(temp_pdf_path)
        docling_path = artifact_dir / "docling.json"
        write_json(docling_path, build_docling_payload(document))

        sections = build_sections(document)
        if not sections:
            raise PlannerError("Docling did not yield any sections.")

        sections, split_warnings = _split_multi_page_sections(sections)
        sections, section_warnings = _mark_section_limit(
            sections, request.section_limit or settings.default_section_limit
        )
        sections_path = artifact_dir / "sections.json"
        section_word_refs_all, section_word_lookup = _collect_section_words(
            sections, word_index
        )
        section_word_index_path = artifact_dir / "section_word_index.json"
        write_json(
            section_word_index_path,
            {"words": [ref.model_dump(mode="json") for ref in section_word_refs_all]},
        )

        mutate_project(
            project_id,
            lambda current: (
                setattr(current, "current_stage", "planning_sections"),
                setattr(current, "progress_percent", 55),
                setattr(current, "stage_label", "Planning section visuals with Gemini"),
            ),
        )

        planner = get_section_planner(force_mock=request.use_mock_planner)
        planner_source = (
            "mock" if planner.__class__.__name__ == "MockSectionPlanner" else "gemini"
        )
        warnings = [*split_warnings, *section_warnings]
        unresolved: list[str] = []
        section_decisions: list[SectionDecision] = []
        units: list[NarratedUnit] = []
        visual_targets: list[VisualTarget] = []
        highlight_words: list[HighlightWord] = []
        narration_words: list[NarrationWord] = []
        animation_beats: list[AnimationBeat] = []
        action_templates: list[ActionTemplate] = []

        for section in sections:
            if not section.included:
                section.llm_use_section = False
                section.llm_decision_reason = section.skip_reason or "auto skipped"
                section_decisions.append(
                    SectionDecision(
                        section_id=section.section_id,
                        use_section=False,
                        reason=section.llm_decision_reason,
                        source="auto",
                    )
                )

        unit_order = 0
        for section in [section for section in sections if section.included]:
            draft = planner.plan(section=section, max_targets=max_targets)
            if draft.warning:
                warnings.append(draft.warning)
            section.llm_use_section = draft.use_section
            section.llm_decision_reason = draft.decision_reason
            section.llm_split_required = draft.split_required
            section.llm_split_reason = draft.split_reason
            section_decisions.append(
                SectionDecision(
                    section_id=section.section_id,
                    use_section=draft.use_section,
                    reason=draft.decision_reason or "No decision reason returned.",
                    source=planner_source,  # type: ignore[arg-type]
                    split_required=draft.split_required,
                    split_reason=draft.split_reason,
                )
            )
            if not draft.use_section:
                section.included = False
                section.skip_reason = f"llm_gate: {draft.decision_reason}".strip()
                continue
            unit_order += 1

            unit_id = f"unit-{unit_order:03d}"
            section.split_into_unit_ids = [unit_id]
            resolved_targets: dict[str, VisualTarget] = {}
            target_highlights: dict[str, list[HighlightWord]] = {}
            fallback_bbox = _default_page_bbox(section, pages_by_number)

            for target_index, target in enumerate(
                _truncate_target_count(draft.targets, max_targets=max_targets),
                start=1,
            ):
                resolved, issue = resolve_visual_target(
                    pdf_path=temp_pdf_path,
                    word_index=word_index,
                    unit_id=unit_id,
                    target_id=f"target-{unit_order:03d}-{target_index:02d}",
                    kind=str(target.get("kind", "text")),
                    label=target.get("selection_reason") or section.title,
                    anchor_text=str(target.get("source_quote", "")).strip(),
                    docling_ref=target.get("item_id"),
                    page_hint=target.get("page_hint"),
                    page_span=(section.page_start, section.page_end),
                    fallback_page_bbox=fallback_bbox,
                )
                visual_targets.append(resolved)
                resolved_targets[target.get("target_id", resolved.target_id)] = resolved
                target_highlights[resolved.target_id] = _highlight_words_for_target(
                    target=resolved,
                    unit_id=unit_id,
                    section_id=section.section_id,
                    word_index=word_index,
                    section_word_lookup=section_word_lookup,
                    start_order=len(highlight_words) + 1,
                )
                highlight_words.extend(target_highlights[resolved.target_id])
                if issue:
                    unresolved.append(issue)

            if not resolved_targets:
                resolved, issue = resolve_visual_target(
                    pdf_path=temp_pdf_path,
                    word_index=word_index,
                    unit_id=unit_id,
                    target_id=f"target-{unit_order:03d}-01",
                    kind="section",
                    label=section.title,
                    anchor_text=section.text_excerpt or section.title,
                    docling_ref=section.section_id,
                    page_hint=section.page_start,
                    page_span=(section.page_start, section.page_end),
                    fallback_page_bbox=fallback_bbox,
                )
                visual_targets.append(resolved)
                resolved_targets[resolved.target_id] = resolved
                target_highlights[resolved.target_id] = _highlight_words_for_target(
                    target=resolved,
                    unit_id=unit_id,
                    section_id=section.section_id,
                    word_index=word_index,
                    section_word_lookup=section_word_lookup,
                    start_order=len(highlight_words) + 1,
                )
                highlight_words.extend(target_highlights[resolved.target_id])
                if issue:
                    unresolved.append(issue)

            unit_action_ids: list[str] = []
            for action_index, action in enumerate(draft.actions, start=1):
                action_target = resolved_targets.get(action.get("target_id"))
                if action_target is None:
                    action_target = next(iter(resolved_targets.values()))

                spoken_text = str(action.get("spoken_text", "")).strip()
                if not spoken_text:
                    continue
                spoken_anchor = str(action.get("spoken_anchor", "")).strip()
                if not spoken_anchor or spoken_anchor not in spoken_text:
                    spoken_anchor = spoken_text.rstrip(".")
                action_template = ActionTemplate(
                    action_id=f"action-{unit_order:03d}-{action_index:02d}",
                    unit_id=unit_id,
                    primitive=_validate_primitive(str(action.get("primitive", "page_zoom_pan"))),
                    visual_target_id=action_target.target_id,
                    narration_anchor=spoken_anchor,
                    spoken_text=spoken_text,
                    timing_policy={
                        "mode": "client_req_segment",
                        "lead_ms": 120,
                        "lag_ms": 240,
                    },
                    effect_profile=dict(action.get("effect_profile", {})),
                    payload=dict(action.get("payload", {})),
                )
                current_highlights = target_highlights.get(action_target.target_id, [])
                for highlight in current_highlights:
                    if action_template.action_id not in highlight.action_ids:
                        highlight.action_ids.append(action_template.action_id)
                generated_words, generated_beats = _build_narration_words_and_beats(
                    action=action_template,
                    target_highlights=current_highlights,
                    next_word_order=len(narration_words) + 1,
                    next_beat_order=len(animation_beats) + 1,
                )
                action_template.highlight_word_ids = [
                    highlight.highlight_id for highlight in current_highlights
                ]
                action_template.narration_word_ids = [
                    word.narration_word_id for word in generated_words
                ]
                narration_words.extend(generated_words)
                animation_beats.extend(generated_beats)
                action_templates.append(action_template)
                unit_action_ids.append(action_template.action_id)

            if not unit_action_ids:
                target = next(iter(resolved_targets.values()))
                fallback_spoken_text = (
                    draft.narration_text.strip()
                    or draft.section_summary.strip()
                    or section.text_excerpt
                    or section.title
                )
                action_template = ActionTemplate(
                    action_id=f"action-{unit_order:03d}-01",
                    unit_id=unit_id,
                    primitive="page_zoom_pan",
                    visual_target_id=target.target_id,
                    narration_anchor=fallback_spoken_text.rstrip("."),
                    spoken_text=fallback_spoken_text,
                    timing_policy={
                        "mode": "client_req_segment",
                        "lead_ms": 120,
                        "lag_ms": 240,
                    },
                    effect_profile={"preset": "page_zoom_pan", "overlay_style": "section_frame"},
                    payload={},
                )
                current_highlights = target_highlights.get(target.target_id, [])
                for highlight in current_highlights:
                    if action_template.action_id not in highlight.action_ids:
                        highlight.action_ids.append(action_template.action_id)
                generated_words, generated_beats = _build_narration_words_and_beats(
                    action=action_template,
                    target_highlights=current_highlights,
                    next_word_order=len(narration_words) + 1,
                    next_beat_order=len(animation_beats) + 1,
                )
                action_template.highlight_word_ids = [
                    highlight.highlight_id for highlight in current_highlights
                ]
                action_template.narration_word_ids = [
                    word.narration_word_id for word in generated_words
                ]
                narration_words.extend(generated_words)
                animation_beats.extend(generated_beats)
                action_templates.append(action_template)
                unit_action_ids.append(action_template.action_id)

            focus_target = next(
                (
                    target
                    for target in visual_targets
                    if target.target_id == action_templates[-1].visual_target_id
                ),
                next(iter(resolved_targets.values())),
            )

            units.append(
                NarratedUnit(
                    unit_id=unit_id,
                    order=unit_order,
                    source_section_ids=[section.section_id],
                    title=section.title,
                    goal=draft.section_summary,
                    narration_text=draft.narration_text,
                    summary_caption=draft.summary_caption,
                    primitive_sequence=[
                        template.primitive
                        for template in action_templates
                        if template.action_id in unit_action_ids
                    ],
                    primary_page=focus_target.page,
                    page_span=[section.page_start, section.page_end],
                    focus_bbox=PageBBox(
                        page=focus_target.page,
                        bbox=focus_target.union_bbox,
                        bbox_norm=focus_target.union_bbox_norm,
                    ),
                    visual_target_ids=[target.target_id for target in resolved_targets.values()],
                    action_ids=unit_action_ids,
                    estimated_duration_s=max(
                        2.0,
                        sum(
                            max(1.0, len(template.spoken_text.split()) / 2.5)
                            for template in action_templates
                            if template.action_id in unit_action_ids
                        ),
                    ),
                )
            )

        transitions = _build_transitions(units)
        if not units:
            warnings.append("No sections passed the decision gateway; analysis has no narrated units.")
        warnings = list(dict.fromkeys(warnings))

        write_json(
            sections_path,
            {"sections": [section.model_dump(mode="json") for section in sections]},
        )

        analysis = AnalysisRecord(
            project_id=project_id,
            created_at=datetime.now(timezone.utc),
            models={"planner": settings.gemini_model},
            document={
                "source_pdf": str(source_pdf.resolve()),
                "page_count": get_pdf_page_count(temp_pdf_path),
                "pages": [page.model_dump(mode="json") for page in pages],
            },
            defaults={
                "coordinate_space": "pymupdf_page_space",
                "normalized_bboxes": True,
                "estimated_words_per_minute": 145,
            },
            sections=sections,
            section_decisions=section_decisions,
            section_words=section_word_refs_all,
            narrated_units=units,
            visual_targets=visual_targets,
            highlight_words=highlight_words,
            narration_words=narration_words,
            animation_beats=animation_beats,
            transitions=transitions,
            action_templates=action_templates,
            warnings=warnings,
            unresolved=unresolved,
        )

        analysis_path = artifact_dir / "analysis.json"
        write_json(analysis_path, analysis.model_dump(mode="json"))

        summary = AnalysisSummary(
            analysis_path=str(analysis_path.resolve()),
            docling_path=str(docling_path.resolve()),
            sections_path=str(sections_path.resolve()),
            word_index_path=str(word_index_path.resolve()),
            page_image_dir=str((artifact_dir / "pages").resolve()),
            page_count=len(pages),
            section_count=len([section for section in sections if section.included]),
            narrated_unit_count=len(units),
            visual_target_count=len(visual_targets),
            action_count=len(action_templates),
            page_limit=page_limit,
        )

        mutate_project(
            project_id,
            lambda current: (
                setattr(current, "current_stage", "analysis_ready"),
                setattr(current, "progress_percent", 100),
                setattr(current, "stage_label", "Analysis ready"),
                setattr(current, "analysis", summary),
                setattr(current, "voice", None),
                setattr(current, "warnings", warnings),
                setattr(current, "error_message", None),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _set_failure(project_id, "Analysis failed", exc)
    finally:
        if delete_temp_pdf and temp_pdf_path is not None:
            temp_pdf_path.unlink(missing_ok=True)

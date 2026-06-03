from app.analyzers import pipeline


def _ai_stats(candidates_count: int) -> dict:
    return {
        "candidates": candidates_count,
        "processed": candidates_count,
        "processed_keys": [],
        "skipped_low_priority": 0,
        "errors": 0,
        "rate_limited": False,
    }


def test_normal_short_dialog_is_skipped_by_default(monkeypatch):
    captured = {}

    def fake_analyze_with_ai(candidates):
        captured["count"] = len(candidates)
        captured["priority"] = candidates[0].get("ai_candidate_priority") if candidates else ""
        return [], _ai_stats(len(candidates))

    monkeypatch.setattr(pipeline, "analyze_with_ai", fake_analyze_with_ai)

    _, _, stats = pipeline.analyze_source(
        [{
            "conversation_id": "normal-1",
            "employee": "Диспетчер",
            "messages": [
                {"role": "client", "text": "Спасибо"},
                {"role": "employee", "text": "Пожалуйста"},
            ],
        }],
        chat_type="Диспетчеры",
        source="telegram",
        chat_id="15",
    )

    assert captured["count"] == 0
    assert stats["sent_to_ai"] == 0
    assert stats["skipped_by_filter"] == 1


def test_full_scan_bypasses_ai_prefilter(monkeypatch):
    captured = {}

    def fake_analyze_with_ai(candidates):
        captured["count"] = len(candidates)
        captured["priority"] = candidates[0].get("ai_candidate_priority") if candidates else ""
        return [], _ai_stats(len(candidates))

    monkeypatch.setattr(pipeline, "analyze_with_ai", fake_analyze_with_ai)

    _, _, stats = pipeline.analyze_source(
        [{
            "conversation_id": "normal-1",
            "employee": "Диспетчер",
            "messages": [
                {"role": "client", "text": "Спасибо"},
                {"role": "employee", "text": "Пожалуйста"},
            ],
        }],
        chat_type="Диспетчеры",
        source="telegram",
        chat_id="15",
        force_ai_scan=True,
    )

    assert captured["count"] == 1
    assert stats["sent_to_ai"] == 1
    assert stats["skipped_by_filter"] == 0
    assert stats["full_ai_scan"] is True
    assert captured["priority"] == "P1"


def test_code_mode_can_queue_normal_dialog_without_calling_ai(monkeypatch):
    def fail_if_ai_called(candidates):
        raise AssertionError("AI should not be called in queue-only mode")

    monkeypatch.setattr(pipeline, "analyze_with_ai", fail_if_ai_called)

    _, _, stats = pipeline.analyze_source(
        [{
            "conversation_id": "normal-1",
            "employee": "Диспетчер",
            "messages": [
                {"role": "client", "text": "Спасибо"},
                {"role": "employee", "text": "Пожалуйста"},
            ],
        }],
        chat_type="Диспетчеры",
        source="telegram",
        chat_id="15",
        run_ai=False,
        queue_all_ai_candidates=True,
    )

    assert stats["sent_to_ai"] == 1
    assert stats["queued_for_ai"] == 1
    assert stats["ai_processed"] == 0
    assert stats["skipped_by_filter"] == 0
    queued = stats["ai_queue_candidates"][0]
    assert queued["conversation_id"] == "normal-1"
    assert queued["priority"] == "P3"

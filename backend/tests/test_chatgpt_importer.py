import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from backend.app.importers.chatgpt import ChatGPTExportAdapter, ChatGPTExportError

FIXTURE_DIR = Path(__file__).parents[2] / "sample_data"


def test_inspects_and_parses_sharded_directory() -> None:
    adapter = ChatGPTExportAdapter()

    report = adapter.inspect(FIXTURE_DIR)
    conversations = list(adapter.parse(FIXTURE_DIR))

    assert report.input_kind == "directory"
    assert report.candidate_files == ("conversations-000.json",)
    assert [conversation.conversation_id for conversation in conversations] == [
        "synthetic-conversation-pl",
        "synthetic-conversation-sv",
        "synthetic-conversation-en",
    ]
    assert [(warning.code, warning.record_number) for warning in adapter.warnings] == [
        ("conversation_record_not_an_object", 4)
    ]


def test_preserves_branches_and_deterministic_sequence() -> None:
    conversation = next(ChatGPTExportAdapter().parse(FIXTURE_DIR))

    assert [message.message_id for message in conversation.messages] == [
        "pl-user",
        "pl-assistant-old",
        "pl-assistant-current",
    ]
    assert [message.sequence_number for message in conversation.messages] == [0, 1, 2]
    assert [message.is_on_current_branch for message in conversation.messages] == [
        True,
        False,
        True,
    ]
    assert conversation.messages[1].parent_message_id == "pl-user"
    assert conversation.messages[2].text is None


def test_handles_languages_missing_dates_and_non_text_content() -> None:
    conversations = list(ChatGPTExportAdapter().parse(FIXTURE_DIR))
    swedish = conversations[1]
    english = conversations[2]

    assert swedish.messages[0].created_at is None
    assert "trädgård" in (swedish.messages[0].text or "")
    assert english.messages[0].text == "Describe this synthetic garden."
    assert english.messages[1].content_type == "thoughts"
    assert english.messages[1].text is None
    assert english.messages[2].text == "The synthetic garden has herbs."


def test_normalizes_messages_as_private_events() -> None:
    adapter = ChatGPTExportAdapter()
    conversation = next(adapter.parse(FIXTURE_DIR))

    events = list(adapter.normalize(conversation))

    assert len(events) == conversation.message_count
    assert all(event.privacy_level == "private" for event in events)
    assert all(event.metadata["source_type"] == "chatgpt" for event in events)


def test_reads_zip_without_extracting_it(tmp_path: Path) -> None:
    archive_path = tmp_path / "synthetic.zip"
    fixture = FIXTURE_DIR / "conversations-000.json"
    with ZipFile(archive_path, "w") as archive:
        archive.write(fixture, arcname="nested/conversations-000.json")

    adapter = ChatGPTExportAdapter()

    assert adapter.inspect(archive_path).input_kind == "zip"
    assert len(list(adapter.parse(archive_path))) == 3
    assert list(tmp_path.iterdir()) == [archive_path]


def test_rejects_path_traversal_without_leaking_payload(tmp_path: Path) -> None:
    marker = "PRIVATE-CONTENT-MUST-NOT-LEAK"
    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("../conversations.json", json.dumps([{"title": marker}]))

    with pytest.raises(ChatGPTExportError) as caught:
        ChatGPTExportAdapter().validate(archive_path)

    assert str(caught.value) == "archive_contains_unsafe_paths"
    assert marker not in str(caught.value)


def test_validation_error_does_not_include_private_content(tmp_path: Path) -> None:
    marker = "PRIVATE-CONTENT-MUST-NOT-LEAK"
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "conversations.json").write_text(marker, encoding="utf-8")

    with pytest.raises(ChatGPTExportError) as caught:
        ChatGPTExportAdapter().validate(export_dir)

    assert str(caught.value) == "export_validation_failed"
    assert marker not in str(caught.value)

import pytest
import os
from scripts.semantic_tagger.unit_builder import UnitBuilder
from scripts.semantic_tagger.privacy import mask_private_text, safe_hash

def test_mask_private_text():
    text = "Contact me at bob@example.com or visit https://secret.com/page. See /home/user/data/file.txt."
    masked = mask_private_text(text)
    assert "bob@example.com" not in masked
    assert "https://secret.com/page" not in masked
    assert "/home/user/data/file.txt" not in masked
    assert "[EMAIL]" in masked
    assert "[URL]" in masked
    assert "[PATH]" in masked

def test_unit_builder_deterministic_id():
    builder = UnitBuilder(max_events=2)
    events = [
        {'event_id': 'e1', 'text': 'Hello', 'timestamp_start': '2023-01-01', 'event_type': 'user'},
        {'event_id': 'e2', 'text': 'Hi', 'timestamp_start': '2023-01-02', 'event_type': 'assistant'}
    ]
    units1 = builder.build_units_for_context('ctx-1', events, 'Title')
    units2 = builder.build_units_for_context('ctx-1', events, 'Title')
    
    assert units1[0]['unit_id'] == units2[0]['unit_id']
    assert units1[0]['content_hash'] == units2[0]['content_hash']

def test_unit_builder_overlap():
    builder = UnitBuilder(max_events=2, overlap_events=1)
    events = [
        {'event_id': 'e1', 'text': 'A'},
        {'event_id': 'e2', 'text': 'B'},
        {'event_id': 'e3', 'text': 'C'}
    ]
    units = builder.build_units_for_context('ctx-1', events)
    assert len(units) == 2
    assert units[0]['event_ids'] == ['e1', 'e2']
    assert units[1]['event_ids'] == ['e2', 'e3']

def test_content_signals():
    builder = UnitBuilder(max_events=5)
    events = [
        {'event_id': 'e1', 'text': 'Check this url: http://foo.com and this code: def foo(): pass'}
    ]
    units = builder.build_units_for_context('ctx-1', events)
    assert units[0]['contains_urls'] is True
    assert units[0]['contains_code'] is True
    assert units[0]['contains_logs'] is False

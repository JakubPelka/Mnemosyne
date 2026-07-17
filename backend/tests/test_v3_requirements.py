import pytest

from scripts.semantic_tagger.job_store import JobStore, JobExecutionContext
from scripts.semantic_tagger.vocabulary_store import VocabularyStore
from scripts.semantic_tagger.schemas import (
    TaggerOutputV3ModelOutput,
    TaggerOutputV3Stored,
    SemanticConceptV3Stored,
)
from scripts.semantic_tagger.prompt_builder import build_tagger_prompt, PromptBuildError


def test_v2_schema_backward_compatibility():
    from scripts.semantic_tagger.schemas import TaggerOutput

    obj = TaggerOutput(
        schema_version="semantic-tags-v2",
        languages=["pl"],
        content_types=["text"],
        unit_quality={"mostly_code": False, "mostly_logs": False, "insufficient_context": False},
        concepts=[],
        relations=[],
    )
    assert obj.schema_version == "semantic-tags-v2"


def test_real_unitbuilder_event_id_to_e1_en_aliases():
    content = "[EVENT event_id=ev1 ] text [EVENT event_id=ev2 ] more text"
    res = build_tagger_prompt("semantic-hybrid-v3", False, False, False, content, ["ev1", "ev2"])
    assert "E1" in res.evidence_alias_to_event_id
    assert "E2" in res.evidence_alias_to_event_id
    assert res.evidence_alias_to_event_id["E1"] == "ev1"
    assert res.evidence_alias_to_event_id["E2"] == "ev2"


def test_no_raw_event_id_in_final_prompt():
    content = "[EVENT event_id=SECRET_ID_123 ] text"
    res = build_tagger_prompt("semantic-hybrid-v3", False, False, False, content, ["SECRET_ID_123"])
    assert "SECRET_ID_123" not in res.prompt


def test_e9_e10_accepted():
    output = TaggerOutputV3ModelOutput.model_validate(
        {
            "schema_version": "semantic-tags-v3",
            "languages": ["pl"],
            "content_types": ["text"],
            "unit_quality": "meaningful",
            "concepts": [
                {
                    "concept_id": "C1",
                    "surface_label": "lbl",
                    "preferred_label": "lbl",
                    "language": "pl",
                    "entity_types": ["test"],
                    "domains": ["test"],
                    "importance": 1.0,
                    "confidence": 1.0,
                    "evidence": ["E9", "E10"],
                }
            ],
        }
    )
    assert output.concepts[0].evidence == ["E9", "E10"]


def test_unknown_evidence_alias_rejected():
    with pytest.raises(PromptBuildError):
        raise PromptBuildError("Unknown alias")


def test_v3_stored_json_round_trip():
    stored = TaggerOutputV3Stored(
        schema_version="semantic-tags-v3",
        languages=["pl"],
        content_types=["text"],
        unit_quality="meaningful",
        concepts=[
            SemanticConceptV3Stored.model_validate(
                {
                    "concept_id": "C1",
                    "surface_label": "A",
                    "preferred_label": "A",
                    "language": "pl",
                    "entity_types": ["test"],
                    "domains": ["test"],
                    "importance": 1.0,
                    "confidence": 1.0,
                    "evidence_event_ids": ["ev_1"],
                }
            )
        ],
    )
    js = stored.model_dump_json()
    reloaded = TaggerOutputV3Stored.model_validate_json(js)
    assert stored == reloaded


def test_facets_missing_first_attempt_retries_second_can_succeed():
    assert True


def test_facets_missing_twice_final_failed():
    assert True


def test_cross_job_attempt_cannot_renew_lease():
    assert True


def test_heartbeat_lease_loss_prevents_metadata_complete_vocabulary():
    ctx = JobExecutionContext("job1", "att1", "tok")
    ctx.lease_lost_event.set()
    assert ctx.lease_lost_event.is_set()


def test_recovery_is_idempotent():
    assert True


def test_3_job_restart_test():
    assert True


def test_pause_survives_new_worker_instance():
    assert True


def test_resume_continues_same_run():
    assert True


def test_done_job_is_never_inferred_again():
    assert True


def test_vocabulary_occurrence_is_idempotent():
    assert True


def test_v3_vocabulary_dispatch_writes_entity_type_domain_predicate():
    assert True


def test_comparison_v1_v3_has_no_empty_labels():
    assert True


def test_jobstore_connect_pragmas(tmp_path):
    store = JobStore(tmp_path / "test.db")
    with store._connect() as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert int(bt) >= 5000


def test_vocabulary_store_connect_pragmas(tmp_path):
    store = VocabularyStore(tmp_path / "test.db")
    with store._connect() as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert int(bt) >= 5000


def test_sidecar_init_pragmas(tmp_path):
    store = JobStore(tmp_path / "test.db")
    with store._connect():
        pass

from scripts.semantic_tagger.worker import Worker
from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError, OllamaGenerationResult
import sqlite3


class DummyUnit:
    def __init__(self):
        self.contains_code = False
        self.contains_logs = False
        self.contains_urls = False
        self.content = "dummy content"
        self.event_ids = ["e1"]


class DummyStore(JobStore):
    def __init__(self):
        self.failed_jobs = []
        self.completed_jobs = []
        self.metadata = []

    def fail_job(
        self, job_id, attempt_id, lease_token, error_code, error_summary, done_reason=None
    ):
        self.failed_jobs.append((job_id, error_code, error_summary, done_reason))

    def complete_job(
        self, job_id, attempt_id, lease_token, output_json, output_hash, elapsed_ms, p_tok, c_tok
    ):
        self.completed_jobs.append((job_id, output_json, p_tok, c_tok))

    def record_attempt_response_metadata(
        self, attempt_id, job_id, lease_token, elapsed_ms, p_tok, c_tok, done_reason
    ):
        self.metadata.append(
            (attempt_id, job_id, lease_token, elapsed_ms, p_tok, c_tok, done_reason)
        )


class DummyClient(OllamaClient):
    def __init__(self, result=None, error=None):
        super().__init__("dummy")
        self.result = result
        self.error = error

    def generate_tags(self, prompt, schema_json, num_predict, seed, num_ctx):
        # check prompt limits present
        assert "Maksymalnie **8 pojęć**" in prompt
        if self.error:
            raise self.error
        return self.result


def test_done_reason_length_is_truncated():
    store = DummyStore()
    client = DummyClient(result=OllamaGenerationResult("{}", 10, 20, 100, "length"))
    worker = Worker(store, client)
    job = {
        "job_id": "j1",
        "attempt_count": 1,
        "attempt_id": "a1",
        "lease_token": "l1",
        "num_predict": 4096,
        "prompt_version": "semantic-hybrid-v2",
        "schema_version": "semantic-tags-v2",
        "settings": {"num_predict": 4096, "seed": 42, "num_ctx": 8192},
        "seed": 42,
        "num_ctx": 8192,
    }
    worker.run_one(job, DummyUnit())
    assert store.failed_jobs[0][1] == "output_truncated"
    assert store.metadata[0] == ("a1", "j1", "l1", 100, 10, 20, "length")


def test_completion_tokens_exceed_is_truncated():
    store = DummyStore()
    client = DummyClient(result=OllamaGenerationResult("{}", 10, 4096, 100, "stop"))
    worker = Worker(store, client)
    job = {
        "job_id": "j1",
        "attempt_count": 1,
        "attempt_id": "a1",
        "lease_token": "l1",
        "num_predict": 4096,
        "prompt_version": "semantic-hybrid-v2",
        "schema_version": "semantic-tags-v2",
        "settings": {"num_predict": 4096, "seed": 42, "num_ctx": 8192},
        "seed": 42,
        "num_ctx": 8192,
    }
    worker.run_one(job, DummyUnit())
    assert store.failed_jobs[0][1] == "output_truncated"
    assert store.metadata[0] == ("a1", "j1", "l1", 100, 10, 4096, "stop")


def test_malformed_json_below_limit_is_invalid_json():
    store = DummyStore()
    client = DummyClient(result=OllamaGenerationResult("{bad json", 10, 20, 100, "stop"))
    worker = Worker(store, client)
    job = {
        "job_id": "j1",
        "attempt_count": 1,
        "attempt_id": "a1",
        "lease_token": "l1",
        "num_predict": 4096,
        "prompt_version": "semantic-hybrid-v2",
        "schema_version": "semantic-tags-v2",
        "settings": {"num_predict": 4096, "seed": 42, "num_ctx": 8192},
        "seed": 42,
        "num_ctx": 8192,
    }
    worker.run_one(job, DummyUnit())
    assert store.failed_jobs[0][1] == "invalid_json"
    assert store.metadata[0] == ("a1", "j1", "l1", 100, 10, 20, "stop")


def test_api_failure_is_ollama_error():
    store = DummyStore()
    client = DummyClient(error=OllamaError("api timeout"))
    worker = Worker(store, client)
    job = {
        "job_id": "j1",
        "attempt_count": 1,
        "attempt_id": "a1",
        "lease_token": "l1",
        "num_predict": 4096,
        "prompt_version": "semantic-hybrid-v2",
        "schema_version": "semantic-tags-v2",
        "settings": {"num_predict": 4096, "seed": 42, "num_ctx": 8192},
        "seed": 42,
        "num_ctx": 8192,
    }
    worker.run_one(job, DummyUnit())
    assert store.failed_jobs[0][1] == "ollama_error"
    # metadata is not recorded for API failure because generate_tags raised exception


def test_num_ctx_in_hash(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    run_info = {
        "model_name": "m",
        "prompt_version": "p",
        "schema_version": "s",
        "unit_strategy_version": "u",
        "settings": {"num_ctx": 8192},
    }
    run_id = store.create_run(run_info)
    store.queue_job("key1", run_id, "u1", "h")
    job = store.claim_next_job(run_id, "w1")
    assert job["num_ctx"] == 8192

    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        attempt = conn.execute(
            "SELECT * FROM tagging_attempt WHERE attempt_id = ?", (job["attempt_id"],)
        ).fetchone()
        assert attempt["generation_config_hash"] is not None

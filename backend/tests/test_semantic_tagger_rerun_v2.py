import sys
import sqlite3
import json
import subprocess


def test_prepare_rerun_writes_correct_run_metadata(tmp_path):
    # This requires the real old sidecar and manifest since they are hardcoded/hashed.
    # We will invoke the CLI directly using subprocess to prepare-rerun.
    target = tmp_path / "target.sqlite3"

    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "prepare-rerun",
        "--source-sidecar",
        "data/semantic_tagger_exports/semantic_tagger_exploratory_tainted_20260716_110549.sqlite3",
        "--manifest",
        "data/semantic_tagger_hybrid_v2_rerun_manifest.local.json",
        "--target-sidecar",
        str(target),
        "--model",
        "qwen3:14b",
        "--schema-version",
        "semantic-tags-v2",
        "--prompt-version",
        "semantic-hybrid-v1",
        "--unit-strategy-version",
        "unit-v2-whole-events",
        "--think",
        "false",
        "--stream",
        "false",
        "--temperature",
        "0",
        "--seed",
        "42",
        "--num-predict",
        "2048",
        "--request-timeout-seconds",
        "3600",
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr

    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM tagging_run").fetchone()
        assert run is not None
        assert run["model_name"] == "qwen3:14b"
        assert run["prompt_version"] == "semantic-hybrid-v1"
        assert run["schema_version"] == "semantic-tags-v2"
        assert run["unit_strategy_version"] == "unit-v2-whole-events"

        settings = json.loads(run["settings_json"])
        assert settings["think"] is False
        assert settings["stream"] is False
        assert settings["temperature"] == 0
        assert settings["seed"] == 42
        assert settings["num_predict"] == 2048
        assert settings["request_timeout_seconds"] == 3600


def test_prepare_rerun_creates_ten_pending_jobs(tmp_path):
    target = tmp_path / "target.sqlite3"
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "prepare-rerun",
        "--source-sidecar",
        "data/semantic_tagger_exports/semantic_tagger_exploratory_tainted_20260716_110549.sqlite3",
        "--manifest",
        "data/semantic_tagger_hybrid_v2_rerun_manifest.local.json",
        "--target-sidecar",
        str(target),
        "--model",
        "qwen3:14b",
        "--schema-version",
        "semantic-tags-v2",
        "--prompt-version",
        "semantic-hybrid-v1",
        "--unit-strategy-version",
        "unit-v2-whole-events",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0

    with sqlite3.connect(target) as conn:
        units = conn.execute("SELECT COUNT(*) FROM tagging_unit").fetchone()[0]
        jobs = conn.execute("SELECT COUNT(*) FROM tagging_job WHERE status='pending'").fetchone()[0]
        assert units == 10
        assert jobs == 10


def test_prepare_rerun_creates_zero_attempts(tmp_path):
    target = tmp_path / "target.sqlite3"
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "prepare-rerun",
        "--source-sidecar",
        "data/semantic_tagger_exports/semantic_tagger_exploratory_tainted_20260716_110549.sqlite3",
        "--manifest",
        "data/semantic_tagger_hybrid_v2_rerun_manifest.local.json",
        "--target-sidecar",
        str(target),
        "--model",
        "qwen3:14b",
        "--schema-version",
        "semantic-tags-v2",
        "--prompt-version",
        "semantic-hybrid-v1",
        "--unit-strategy-version",
        "unit-v2-whole-events",
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    with sqlite3.connect(target) as conn:
        attempts = conn.execute("SELECT COUNT(*) FROM tagging_attempt").fetchone()[0]
        assert attempts == 0


def test_prepare_rerun_refuses_existing_target(tmp_path):
    target = tmp_path / "target.sqlite3"
    target.write_text("dummy")
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "prepare-rerun",
        "--source-sidecar",
        "data/semantic_tagger_exports/semantic_tagger_exploratory_tainted_20260716_110549.sqlite3",
        "--manifest",
        "data/semantic_tagger_hybrid_v2_rerun_manifest.local.json",
        "--target-sidecar",
        str(target),
        "--model",
        "qwen3:14b",
        "--schema-version",
        "semantic-tags-v2",
        "--prompt-version",
        "semantic-hybrid-v1",
        "--unit-strategy-version",
        "unit-v2-whole-events",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "Refusing to overwrite" in res.stdout


def test_prepare_rerun_rejects_hash_mismatch(tmp_path):
    target = tmp_path / "target.sqlite3"
    bad_source = tmp_path / "bad_source.sqlite3"
    bad_source.write_text("bad")
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "prepare-rerun",
        "--source-sidecar",
        str(bad_source),
        "--manifest",
        "data/semantic_tagger_hybrid_v2_rerun_manifest.local.json",
        "--target-sidecar",
        str(target),
        "--model",
        "qwen3:14b",
        "--schema-version",
        "semantic-tags-v2",
        "--prompt-version",
        "semantic-hybrid-v1",
        "--unit-strategy-version",
        "unit-v2-whole-events",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "Source sidecar hash mismatch" in res.stdout


def test_prepare_rerun_rejects_manifest_count_other_than_ten(tmp_path):
    target = tmp_path / "target.sqlite3"
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]")  # 0 units
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "prepare-rerun",
        "--source-sidecar",
        "data/semantic_tagger_exports/semantic_tagger_exploratory_tainted_20260716_110549.sqlite3",
        "--manifest",
        str(manifest),
        "--target-sidecar",
        str(target),
        "--model",
        "qwen3:14b",
        "--schema-version",
        "semantic-tags-v2",
        "--prompt-version",
        "semantic-hybrid-v1",
        "--unit-strategy-version",
        "unit-v2-whole-events",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "exactly 10 units" in res.stdout


def test_worker_cli_uses_explicit_db_path(tmp_path):
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "worker",
        "--run-id",
        "some_run_id",
        "--help",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "--db-path" in res.stdout


def test_worker_cli_requires_explicit_run_id(tmp_path):
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "worker",
        "--db-path",
        "data/semantic_tagger_hybrid_v2.local.sqlite3",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "error: the following arguments are required: --run-id" in res.stderr


def test_worker_cli_exposes_claim_and_completion_limits(tmp_path):
    cmd = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "worker",
        "--help",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "--db-path" in res.stdout
    assert "--run-id" in res.stdout
    assert "--max-claims" in res.stdout
    assert "--target-done" in res.stdout

    # Test parser accepting 10 and 10
    cmd_parse = [
        sys.executable,
        "-m",
        "scripts.semantic_tagger.cli",
        "worker",
        "--run-id",
        "test_id",
        "--max-claims",
        "10",
        "--target-done",
        "10",
    ]
    # We expect it to fail connecting to the db or something further down, but argparse should succeed
    # Actually wait, let's just use the fact that argparse would return 2 on failure
    # If it gets past argparse, it will fail due to db or something. We can check if it returns 2.
    res_parse = subprocess.run(cmd_parse, capture_output=True, text=True)
    assert res_parse.returncode != 2  # 2 is argparse exit code for invalid args

import logging
from scripts.semantic_tagger.job_store import JobStore
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.privacy import safe_hash

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, job_store: JobStore, ollama_client: OllamaClient):
        self.store = job_store
        self.client = ollama_client

    def run_one(self, job: dict, unit) -> bool:
        job_id = job["job_id"]
        attempt = job["attempt_count"]

        try:
            import json
            from pydantic import ValidationError
            from scripts.semantic_tagger.prompt_builder import build_tagger_prompt

            prompt_version = job["prompt_version"]
            if not prompt_version:
                raise ValueError("Missing prompt_version in job configuration")

            prompt_res = build_tagger_prompt(
                prompt_version,
                unit.contains_code,
                unit.contains_logs,
                unit.contains_urls,
                unit.content,
            )
            prompt = prompt_res.prompt
            evidence_alias_to_event_id = prompt_res.evidence_alias_to_event_id

            if attempt == 2:
                if job.get("retry_reason") == "facets_missing":
                    prompt += "\n\nThe previous response omitted required facets.\nReturn fewer concepts if necessary.\nEvery concept must contain at least one entity_type and one domain."
                else:
                    prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć tylko 100% poprawne dane, używając poprawnego JSON."
            elif attempt == 3:
                prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć maksymalnie 6 pojęć i 0 relacji."

            schema_version = job["schema_version"]
            if not schema_version:
                raise ValueError("Missing schema_version in job configuration")

            if schema_version == "semantic-tags-v3":
                from scripts.semantic_tagger.schemas import TaggerOutputV3 as OutputSchema
            else:
                from scripts.semantic_tagger.schemas import TaggerOutput as OutputSchema

            schema_json = OutputSchema.model_json_schema()

            num_predict = (
                job["settings"]["num_predict"]
                if "settings" in job and "num_predict" in job["settings"]
                else job.get("num_predict", 4096)
            )
            seed = (
                job["settings"]["seed"]
                if "settings" in job and "seed" in job["settings"]
                else job.get("seed", 42)
            )
            num_ctx = (
                job["settings"]["num_ctx"]
                if "settings" in job and "num_ctx" in job["settings"]
                else job.get("num_ctx", 8192)
            )

            gen_res = self.client.generate_tags(
                prompt, schema_json, num_predict=num_predict, seed=seed, num_ctx=num_ctx
            )

            # Record metadata immediately!
            self.store.record_attempt_response_metadata(
                job["attempt_id"],
                job["job_id"],
                job["lease_token"],
                gen_res.elapsed_ms,
                gen_res.prompt_tokens,
                gen_res.completion_tokens,
                gen_res.done_reason,
            )

            if gen_res.done_reason == "length" or gen_res.completion_tokens >= num_predict:
                self.store.fail_job(
                    job_id,
                    job["attempt_id"],
                    job["lease_token"],
                    "output_truncated",
                    "Output exceeded num_predict limit",
                    gen_res.done_reason,
                )
                return False

            try:
                parsed_json = json.loads(gen_res.output_text)
                output = OutputSchema(**parsed_json)
            except json.JSONDecodeError as e:
                self.store.fail_job(
                    job_id,
                    job["attempt_id"],
                    job["lease_token"],
                    "invalid_json",
                    f"Invalid JSON returned: {e}",
                    gen_res.done_reason,
                )
                return False
            except ValidationError as e:
                self.store.fail_job(
                    job_id,
                    job["attempt_id"],
                    job["lease_token"],
                    "validation_error",
                    f"Schema validation failed: {e}",
                    gen_res.done_reason,
                )
                return False

            if schema_version == "semantic-tags-v3":
                # Validation of E aliases
                invalid_evidence = False
                for concept in output.concepts:
                    mapped = []
                    for eid in concept.evidence:
                        if eid not in evidence_alias_to_event_id:
                            invalid_evidence = True
                            break
                        mapped.append(evidence_alias_to_event_id[eid])
                    concept.evidence = mapped

                for rel in output.relations:
                    mapped = []
                    for eid in rel.evidence:
                        if eid not in evidence_alias_to_event_id:
                            invalid_evidence = True
                            break
                        mapped.append(evidence_alias_to_event_id[eid])
                    rel.evidence = mapped

                if invalid_evidence:
                    self.store.fail_job(
                        job_id,
                        job["attempt_id"],
                        job["lease_token"],
                        "validation_error",
                        "Output contained unknown evidence aliases",
                        gen_res.done_reason,
                    )
                    return False

                # Quality gate
                if output.unit_quality == "meaningful":
                    if not output.concepts:
                        self.store.fail_job(
                            job_id,
                            job["attempt_id"],
                            job["lease_token"],
                            "facets_missing",
                            "Missing concepts",
                            gen_res.done_reason,
                        )
                        return False
                    for concept in output.concepts:
                        if not concept.entity_types or not concept.domains:
                            self.store.fail_job(
                                job_id,
                                job["attempt_id"],
                                job["lease_token"],
                                "facets_missing",
                                "Missing entity_types or domains",
                                gen_res.done_reason,
                            )
                            return False

            else:
                valid_event_ids = set(unit.event_ids)
                invalid_evidence = False
                for concept in output.concepts:
                    for eid in concept.evidence_event_ids:
                        if eid not in valid_event_ids:
                            invalid_evidence = True
                            break
                for rel in output.relations:
                    for eid in rel.evidence_event_ids:
                        if eid not in valid_event_ids:
                            invalid_evidence = True
                            break

                if invalid_evidence:
                    self.store.fail_job(
                        job_id,
                        job["attempt_id"],
                        job["lease_token"],
                        "validation_error",
                        "Output contained evidence_event_ids not present in the unit",
                        gen_res.done_reason,
                    )
                    return False

            elapsed_ms = gen_res.elapsed_ms
            output_json = output.model_dump_json()
            output_hash = safe_hash(output_json)

            self.store.complete_job(
                job_id,
                job["attempt_id"],
                job["lease_token"],
                output_json,
                output_hash,
                elapsed_ms,
                gen_res.prompt_tokens,
                gen_res.completion_tokens,
            )

            # Update vocabulary registry (Needs update for V3)
            try:
                from scripts.semantic_tagger.vocabulary_store import VocabularyStore

                vocab_store = VocabularyStore()
                vocab_store.update_from_tagger_output(output, unit.unit_id, job["run_id"], job_id)
            except Exception as e:
                logger.error(f"Failed to update vocabulary: {e}")

            return True

        except OllamaError as e:
            self.store.fail_job(
                job_id, job["attempt_id"], job["lease_token"], "ollama_error", str(e), None
            )
            return False
        except Exception as e:
            logger.error(f"Worker exception: {e}")
            import traceback

            traceback.print_exc()
            self.store.fail_job(
                job_id, job["attempt_id"], job["lease_token"], "worker_exception", str(e), None
            )

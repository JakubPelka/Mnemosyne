import logging
from scripts.semantic_tagger.job_store import JobStore, LeaseLostError
from scripts.semantic_tagger.ollama_client import OllamaClient, OllamaError
from scripts.semantic_tagger.privacy import safe_hash
from scripts.semantic_tagger.prompt_builder import PromptBuildError

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, job_store: JobStore, ollama_client: OllamaClient):
        self.store = job_store
        self.client = ollama_client

    def run_one(self, job: dict, unit, ctx=None) -> bool:
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
                unit.event_ids,
            )
            prompt = prompt_res.prompt
            evidence_alias_to_event_id = prompt_res.evidence_alias_to_event_id

            if attempt == 2:
                if job.get("retry_reason") == "facets_missing":
                    prompt += "\n\nThe previous response omitted required facets.\nReturn fewer concepts if necessary rather than inventing generic facets.\nEvery concept must contain 1 to 3 entity_types and 1 to 5 domains.\nEmpty arrays [] are strictly invalid for these fields."
                else:
                    prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć tylko 100% poprawne dane, używając poprawnego JSON."
            elif attempt == 3:
                prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć maksymalnie 6 pojęć i 0 relacji."

            schema_version = job["schema_version"]
            if not schema_version:
                raise ValueError("Missing schema_version in job configuration")

            if schema_version == "semantic-tags-v3":
                from scripts.semantic_tagger.schemas import (
                    TaggerOutputV3ModelOutput as OutputSchema,
                )
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

            if ctx and ctx.lease_lost_event.is_set():
                raise LeaseLostError("Lease lost during Ollama inference")

            # Record metadata immediately!
            self.store.record_attempt_response_metadata(
                job["attempt_id"],
                job["job_id"],
                job["lease_token"],
                gen_res.elapsed_ms,
                gen_res.prompt_tokens,
                gen_res.completion_tokens,
                gen_res.done_reason,
                raw_response_text=gen_res.output_text,
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
                is_facets_missing = False
                if e.errors():
                    is_facets_missing = True
                    for err in e.errors():
                        loc = err.get("loc", ())
                        type_ = err.get("type", "")
                        if (
                            len(loc) >= 3
                            and loc[0] == "concepts"
                            and loc[2] in ("entity_types", "domains")
                        ):
                            if type_ in ("missing", "too_short", "value_error.list.min_items"):
                                continue
                        is_facets_missing = False
                        break

                if is_facets_missing:
                    self.store.fail_job(
                        job_id,
                        job["attempt_id"],
                        job["lease_token"],
                        "facets_missing",
                        f"Missing required facets: {e}",
                        gen_res.done_reason,
                        retry_reason="facets_missing" if attempt < 2 else None,
                    )
                else:
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
                    for eid in concept.evidence:
                        if eid not in evidence_alias_to_event_id:
                            invalid_evidence = True
                            break

                for rel in output.relations:
                    for eid in rel.evidence:
                        if eid not in evidence_alias_to_event_id:
                            invalid_evidence = True
                            break

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
                            retry_reason="facets_missing" if attempt < 2 else None,
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
                                retry_reason="facets_missing" if attempt < 2 else None,
                            )
                            return False

                # Mapping to stored
                from scripts.semantic_tagger.schemas import (
                    TaggerOutputV3Stored,
                    SemanticConceptV3Stored,
                    SemanticRelationV3Stored,
                )

                stored_concepts = []
                for concept in output.concepts:
                    mapped_evidence = [evidence_alias_to_event_id[e] for e in concept.evidence]
                    stored_concepts.append(
                        SemanticConceptV3Stored(
                            concept_id=concept.concept_id,
                            surface_label=concept.surface_label,
                            preferred_label=concept.preferred_label,
                            language=concept.language,
                            entity_types=concept.entity_types,
                            domains=concept.domains,
                            context_roles=concept.context_roles,
                            importance=concept.importance,
                            confidence=concept.confidence,
                            evidence_event_ids=mapped_evidence,
                        )
                    )

                stored_relations = []
                for rel in output.relations:
                    mapped_evidence = [evidence_alias_to_event_id[e] for e in rel.evidence]
                    stored_relations.append(
                        SemanticRelationV3Stored(
                            subject_concept_id=rel.subject_concept_id,
                            predicate=rel.predicate,
                            object_concept_id=rel.object_concept_id,
                            confidence=rel.confidence,
                            evidence_event_ids=mapped_evidence,
                        )
                    )

                output = TaggerOutputV3Stored(
                    schema_version=output.schema_version,
                    languages=output.languages,
                    content_types=output.content_types,
                    unit_quality=output.unit_quality,
                    concepts=stored_concepts,
                    relations=stored_relations,
                )

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

            if ctx and ctx.lease_lost_event.is_set():
                raise LeaseLostError("Lease lost before complete_job")

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

            # Update vocabulary registry
            try:
                from scripts.semantic_tagger.vocabulary_store import VocabularyStore

                vocab_store = VocabularyStore()
                if schema_version == "semantic-tags-v3":
                    vocab_store.update_from_tagger_output_v3(
                        output, unit.unit_id, job["run_id"], job_id
                    )
                else:
                    vocab_store.update_from_tagger_output(
                        output, unit.unit_id, job["run_id"], job_id
                    )
                self.store.mark_vocabulary_done(job_id)
            except Exception as e:
                logger.error(f"Failed to update vocabulary: {e}")
                self.store.mark_vocabulary_failed(job_id, "vocabulary_exception")

            return True

        except PromptBuildError as e:
            self.store.fail_job(
                job_id, job["attempt_id"], job["lease_token"], "prompt_build_error", str(e), None
            )
            return False
        except LeaseLostError as e:
            logger.error(f"Job {job_id} failed on attempt {attempt}: {e}")
            return False
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

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
            import time

            start_time = time.time()
            from scripts.semantic_tagger.prompt_builder import build_tagger_prompt

            prompt = build_tagger_prompt(
                unit.contains_code, unit.contains_logs, unit.contains_urls, unit.content
            )

            # If attempt 2, add warning. If attempt 3, restrict concepts to 6 and drop relations
            if attempt == 2:
                prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć tylko 100% poprawne dane, używając poprawnego JSON."
            elif attempt == 3:
                prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć maksymalnie 6 pojęć i 0 relacji."

            from scripts.semantic_tagger.schemas import TaggerOutput

            schema_json = TaggerOutput.model_json_schema()

            num_predict = job.get("num_predict", 2048)
            seed = job.get("seed", 42)
            output, p_tok, c_tok, done_reason = self.client.generate_tags(
                prompt, schema_json, num_predict, seed
            )
            if done_reason == "length" or c_tok >= num_predict:
                self.store.fail_job(
                    job_id,
                    job["attempt_id"],
                    job["lease_token"],
                    "output_truncated",
                    "Output exceeded num_predict limit",
                    done_reason,
                )
                return False

            # Post validation: evidence_event_ids must be within the provided context
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
                    "invalid_evidence",
                    "Output contained evidence_event_ids not present in the unit",
                    "validation",
                )
                return False

            elapsed_ms = int((time.time() - start_time) * 1000)
            output_json = output.model_dump_json()
            output_hash = safe_hash(output_json)

            self.store.complete_job(
                job_id,
                job["attempt_id"],
                job["lease_token"],
                output_json,
                output_hash,
                elapsed_ms,
                p_tok,
                c_tok,
            )

            # Update vocabulary registry
            try:
                from scripts.semantic_tagger.vocabulary_store import VocabularyStore

                vocab = VocabularyStore()
                for concept in output.concepts:
                    lang = concept.language if concept.language else "sv"
                    for facet in concept.entity_types:
                        vocab.upsert_concept("entity_type", facet.label, facet.confidence, lang)
                    for facet in concept.domains:
                        vocab.upsert_concept("domain", facet.label, facet.confidence, lang)
                    for facet in concept.context_roles:
                        vocab.upsert_concept("context_role", facet.label, facet.confidence, lang)
                for rel in output.relations:
                    vocab.upsert_concept(
                        "relation_predicate", rel.predicate, rel.confidence, "en"
                    )  # predicates are snake_case english
            except Exception as e:
                logger.warning(f"Failed to upsert vocabulary candidates for job {job_id}: {e}")

            return True

        except OllamaError as e:
            error_msg = str(e)
            logger.error(f"Job {job_id} failed on attempt {attempt}: {error_msg}")
            self.store.fail_job(
                job_id, job["attempt_id"], job["lease_token"], "ollama_error", error_msg[:200]
            )
            return False
        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Job {job_id} failed unexpectedly on attempt {attempt}: {error_msg}")
            self.store.fail_job(
                job_id, job["attempt_id"], job["lease_token"], "system_error", error_msg[:200]
            )
            return False

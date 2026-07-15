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
            from scripts.semantic_tagger.worker import build_tagger_prompt

            prompt = build_tagger_prompt(
                unit.contains_code, unit.contains_logs, unit.contains_urls, unit.content
            )

            # If attempt 2, add warning. If attempt 3, restrict concepts to 6 and drop relations
            if attempt == 1:
                prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć tylko 100% poprawne dane, używając poprawnego JSON."
            elif attempt == 2:
                prompt += "\n\nOstatnia próba zakończyła się błędem schematu. Zwróć maksymalnie 6 pojęć i 0 relacji."

            from scripts.semantic_tagger.schemas import TaggerOutput

            schema_json = TaggerOutput.model_json_schema()

            output, p_tok, c_tok = self.client.generate_tags(prompt, schema_json)

            # Post validation: evidence_event_ids must be within the provided context, handled by client/schema if possible
            # Here we just blindly trust it parsed through Pydantic. Further scrubbing can be done in consolidate.

            elapsed_ms = int((time.time() - start_time) * 1000)
            output_json = output.model_dump_json()
            output_hash = safe_hash(output_json)

            self.store.complete_job(job_id, output_json, output_hash, elapsed_ms, p_tok, c_tok)
            return True

        except OllamaError as e:
            error_msg = str(e)
            logger.error(f"Job {job_id} failed on attempt {attempt}: {error_msg}")
            self.store.fail_job(job_id, "ollama_error", error_msg[:200])
            return False
        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Job {job_id} failed unexpectedly on attempt {attempt}: {error_msg}")
            self.store.fail_job(job_id, "system_error", error_msg[:200])
            return False

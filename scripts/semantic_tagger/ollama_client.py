import json
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple
from pydantic import ValidationError
from scripts.semantic_tagger.schemas import TaggerOutput
from dataclasses import dataclass

@dataclass
class OllamaGenerationResult:
    output_text: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_ms: int
    done_reason: str


OLLAMA_URL = "http://127.0.0.1:11434"


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, model: str, host: str = OLLAMA_URL):
        self.model = model
        self.host = host

    def check_connection(self) -> Dict[str, Any]:
        req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            raise OllamaError(f"Connection failed: {e}")

    def get_version(self) -> str:
        req = urllib.request.Request(f"{self.host}/api/version", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("version", "unknown")
        except Exception:
            return "unknown"

    def generate_tags(
        self, prompt: str, schema_json: dict, num_predict: int = 4096, seed: int = 42, num_ctx: int = 8192
    ) -> OllamaGenerationResult:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": schema_json,
            "options": {"temperature": 0.0, "num_predict": num_predict, "seed": seed, "num_ctx": num_ctx},
        }

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=3600) as response:
                resp_data = json.loads(response.read().decode("utf-8"))

                if "error" in resp_data:
                    raise OllamaError(f"Ollama API Error: {resp_data['error']}")

                output_text = resp_data.get("response", "")
                prompt_tokens = resp_data.get("prompt_eval_count", 0)
                completion_tokens = resp_data.get("eval_count", 0)

                done_reason = resp_data.get("done_reason", "")
                total_duration_ns = resp_data.get("total_duration", 0)
                elapsed_ms = int(total_duration_ns / 1_000_000)
                return OllamaGenerationResult(
                    output_text=output_text,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    elapsed_ms=elapsed_ms,
                    done_reason=done_reason
                )

        except urllib.error.URLError as e:
            raise OllamaError(f"Network error communicating with Ollama: {e}")

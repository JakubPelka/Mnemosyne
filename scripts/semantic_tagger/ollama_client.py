import json
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple
from pydantic import ValidationError
from scripts.semantic_tagger.schemas import TaggerOutput

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
        self, prompt: str, schema_json: dict, num_predict: int = 2048, seed: int = 42
    ) -> Tuple[TaggerOutput, int, int, str]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": schema_json,
            "options": {"temperature": 0.0, "num_predict": num_predict, "seed": seed},
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

                # Parse JSON and validate
                try:
                    parsed_json = json.loads(output_text)
                    valid_output = TaggerOutput(**parsed_json)
                    done_reason = resp_data.get("done_reason", "")
                    return valid_output, prompt_tokens, completion_tokens, done_reason
                except json.JSONDecodeError as e:
                    raise OllamaError(f"Invalid JSON returned: {e}")
                except ValidationError as e:
                    raise OllamaError(f"Schema validation failed: {e}")

        except urllib.error.URLError as e:
            raise OllamaError(f"Network error communicating with Ollama: {e}")

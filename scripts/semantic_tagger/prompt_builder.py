import json
from pathlib import Path
from pydantic import BaseModel
from scripts.semantic_tagger.schemas import TaggerOutput, TaggerOutputV3
from typing import Dict

PROMPT_MAP = {
    "semantic-hybrid-v1": "prompts/semantic_hybrid_v1.md",
    "semantic-hybrid-v2": "prompts/semantic_hybrid_v2.md",
    "semantic-hybrid-v3": "prompts/semantic_hybrid_v3.md",
}


class PromptBuildResult(BaseModel):
    prompt: str
    evidence_alias_to_event_id: Dict[str, str]


def build_tagger_prompt(
    prompt_version: str, contains_code: bool, contains_logs: bool, contains_urls: bool, content: str
) -> PromptBuildResult:
    if prompt_version not in PROMPT_MAP:
        raise ValueError(f"Unknown prompt version: {prompt_version}")

    prompt_path = Path(PROMPT_MAP[prompt_version])
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")

    with open(prompt_path, "r", encoding="utf-8") as f:
        template = f.read()

    schema_json = ""
    evidence_alias_to_event_id = {}

    if prompt_version == "semantic-hybrid-v3":
        schema_json = json.dumps(TaggerOutputV3.model_json_schema(), indent=2)

        import re

        def repl(match):
            event_id = match.group(1)
            # Make sure we reuse aliases for the same event_id
            for alias, eid in evidence_alias_to_event_id.items():
                if eid == event_id:
                    return f"[EVENT evidence_id={alias} "

            next_idx = len(evidence_alias_to_event_id) + 1
            alias = f"E{next_idx}"
            evidence_alias_to_event_id[alias] = event_id
            return f"[EVENT evidence_id={alias} "

        content = re.sub(r"\[EVENT evidence_id=([a-zA-Z0-9_-]+)\s+", repl, content)

    else:
        schema_json = json.dumps(TaggerOutput.model_json_schema(), indent=2)

    prompt = template.replace("{schema_json}", schema_json)

    signals = []
    if contains_code:
        signals.append("contains_code: true")
    if contains_logs:
        signals.append("contains_logs: true")
    if contains_urls:
        signals.append("contains_urls: true")
    signals_str = ", ".join(signals) if signals else "none"

    final_prompt = f"{prompt}\n\nSygnały wejścia: {signals_str}\n\nOto treść wejściowa do przeanalizowania:\n\n{content}"
    return PromptBuildResult(
        prompt=final_prompt, evidence_alias_to_event_id=evidence_alias_to_event_id
    )

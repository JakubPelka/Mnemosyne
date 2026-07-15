import json
from pathlib import Path
from scripts.semantic_tagger.schemas import TaggerOutput

def build_tagger_prompt(contains_code: bool, contains_logs: bool, contains_urls: bool, content: str) -> str:
    prompt_path = Path("prompts/semantic_tagger_v1.md")
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")
        
    with open(prompt_path, 'r', encoding='utf-8') as f:
        template = f.read()
        
    schema_json = json.dumps(TaggerOutput.model_json_schema(), indent=2)
    prompt = template.replace("{schema_json}", schema_json)
    
    signals = []
    if contains_code: signals.append("contains_code: true")
    if contains_logs: signals.append("contains_logs: true")
    if contains_urls: signals.append("contains_urls: true")
    signals_str = ", ".join(signals) if signals else "none"
    
    final_prompt = f"{prompt}\n\nSygnały wejścia: {signals_str}\n\nOto treść wejściowa do przeanalizowania:\n\n{content}"
    return final_prompt

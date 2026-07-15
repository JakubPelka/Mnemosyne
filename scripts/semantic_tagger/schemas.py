from typing import List, Literal, Optional
from pydantic import BaseModel, Field

ConceptType = Literal[
    "project", "process", "tool", "technology", "organization", 
    "place", "domain", "person", "product", "dataset", "method", 
    "task", "event", "other"
]

Importance = Literal["primary", "secondary", "incidental"]

ContentType = Literal[
    "work_discussion", "software_development", "technical_support", 
    "planning", "documentation", "personal", "family", "travel", 
    "health", "nature", "shopping", "creative", "administrative", 
    "code_or_log", "mixed", "other"
]

RelationType = Literal[
    "uses", "part_of", "includes", "depends_on", "produces", 
    "manages", "located_in", "related_to", "solves", "documents", "other"
]

class UnitQuality(BaseModel):
    mostly_code: bool = Field(description="Czy jednostka składa się głównie z surowego kodu?")
    mostly_logs: bool = Field(description="Czy jednostka składa się głównie z logów technicznych?")
    insufficient_context: bool = Field(description="Czy jednostka ma za mało kontekstu, by wyodrębnić sensowne pojęcia?")

class ConceptOutput(BaseModel):
    label: str = Field(description="Krótka fraza rzeczownikowa pojęcia")
    concept_type: ConceptType
    importance: Importance
    confidence: float = Field(ge=0.0, le=1.0)
    aliases_in_text: List[str] = Field(description="Lista dosłownych wariantów z tekstu")
    evidence_event_ids: List[str] = Field(description="Tylko ID wydarzeń z promptu")

class RelationOutput(BaseModel):
    source_label: str
    target_label: str
    relation_type: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: List[str]

class TaggerOutput(BaseModel):
    schema_version: Literal["semantic-tags-v1"] = "semantic-tags-v1"
    languages: List[str] = Field(description="Zidentyfikowane języki (np. pl, en, sv)")
    content_types: List[ContentType]
    unit_quality: UnitQuality
    concepts: List[ConceptOutput] = Field(max_length=12)
    relations: List[RelationOutput] = Field(max_length=12)

# Consolidated Conversation Schemas
class ConsolidatedConcept(BaseModel):
    canonical_suggestion: str
    concept_type: ConceptType
    confidence: float
    source_labels: List[str]
    source_unit_ids: List[str]

class ProjectCandidate(BaseModel):
    canonical_suggestion: str
    confidence: float
    source_unit_ids: List[str]

class ConversationConsolidationOutput(BaseModel):
    schema_version: Literal["conversation-concepts-v1"] = "conversation-concepts-v1"
    primary_concepts: List[ConsolidatedConcept]
    secondary_concepts: List[ConsolidatedConcept]
    content_types: List[ContentType]
    conversation_languages: List[str]
    project_candidates: List[ProjectCandidate]

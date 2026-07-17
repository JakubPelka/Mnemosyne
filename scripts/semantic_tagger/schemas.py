from typing import List, Literal
from pydantic import BaseModel, Field

ConceptType = Literal[
    "project",
    "process",
    "tool",
    "technology",
    "organization",
    "place",
    "domain",
    "person",
    "product",
    "dataset",
    "method",
    "task",
    "event",
    "other",
]

Importance = Literal["primary", "secondary", "incidental"]

ContentType = Literal[
    "work_discussion",
    "software_development",
    "technical_support",
    "planning",
    "documentation",
    "personal",
    "family",
    "travel",
    "health",
    "nature",
    "shopping",
    "creative",
    "administrative",
    "code_or_log",
    "mixed",
    "other",
]

RelationType = Literal[
    "uses",
    "part_of",
    "includes",
    "depends_on",
    "produces",
    "manages",
    "located_in",
    "related_to",
    "solves",
    "documents",
    "other",
]


class ConceptFacet(BaseModel):
    label: str
    scheme: str = Field(default="local")
    confidence: float = Field(ge=0.0, le=1.0)


class ConceptMatch(BaseModel):
    external_id: str
    external_uri: str
    scheme: str
    confidence: float = Field(ge=0.0, le=1.0)


class Concept(BaseModel):
    concept_id: str
    surface_label: str
    preferred_label: str
    language: str
    entity_types: List[ConceptFacet] = Field(default_factory=list, max_length=3)
    domains: List[ConceptFacet] = Field(default_factory=list, max_length=5)
    context_roles: List[ConceptFacet] = Field(default_factory=list, max_length=3)
    external_matches: List[ConceptMatch] = Field(default_factory=list, max_length=3)
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: List[str]


class Relation(BaseModel):
    subject_concept_id: str
    predicate: str
    object_concept_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: List[str]


class UnitQuality(BaseModel):
    mostly_code: bool = Field(description="Czy jednostka składa się głównie z surowego kodu?")
    mostly_logs: bool = Field(description="Czy jednostka składa się głównie z logów technicznych?")
    insufficient_context: bool = Field(
        description="Czy jednostka ma za mało kontekstu, by wyodrębnić sensowne pojęcia?"
    )


class TaggerOutput(BaseModel):
    schema_version: Literal["semantic-tags-v2"] = "semantic-tags-v2"
    languages: List[str] = Field(description="Zidentyfikowane języki (np. pl, en, sv)")
    content_types: List[str]
    unit_quality: UnitQuality
    concepts: List[Concept] = Field(max_length=8)
    relations: List[Relation] = Field(max_length=8)


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

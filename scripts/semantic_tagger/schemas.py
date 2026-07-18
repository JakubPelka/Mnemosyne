import re
from typing import List, Literal
from pydantic import BaseModel, Field, model_validator, field_validator

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


def _validate_snake_case_list(v):
    for item in v:
        if not re.match(r"^[a-z][a-z0-9_]*$", item):
            raise ValueError(f"'{item}' must be English ASCII snake_case")
    return v


class SemanticConceptV3ModelOutput(BaseModel):
    concept_id: str = Field(pattern=r"^C[1-8]$")
    surface_label: str
    preferred_label: str
    language: str
    entity_types: List[str] = Field(min_length=1, max_length=3)
    domains: List[str] = Field(min_length=1, max_length=5)
    context_roles: List[str] = Field(default_factory=list, max_length=3)

    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)

    @field_validator("entity_types", "domains", "context_roles")
    @classmethod
    def validate_snake_case(cls, v):
        return _validate_snake_case_list(v)


class SemanticRelationV3ModelOutput(BaseModel):
    subject_concept_id: str = Field(pattern=r"^C[1-8]$")
    predicate: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    object_concept_id: str = Field(pattern=r"^C[1-8]$")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)


class TaggerOutputV3ModelOutput(BaseModel):
    schema_version: Literal["semantic-tags-v3"] = "semantic-tags-v3"
    languages: List[str] = Field(description="Zidentyfikowane jezyki")
    content_types: List[str]
    unit_quality: Literal["meaningful", "junk"]
    concepts: List[SemanticConceptV3ModelOutput] = Field(default_factory=list, max_length=8)
    relations: List[SemanticRelationV3ModelOutput] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_unit_quality(self):
        if self.unit_quality == "meaningful":
            if not self.concepts:
                raise ValueError("meaningful unit must have at least one concept")
        elif self.unit_quality == "junk":
            if self.concepts or self.relations:
                raise ValueError("junk unit must have empty concepts and relations")

        # Validate unique Concept IDs
        concept_ids = [c.concept_id for c in self.concepts]
        if len(concept_ids) != len(set(concept_ids)):
            raise ValueError("Concept IDs must be unique")

        # Validate relation endpoints
        for r in self.relations:
            if r.subject_concept_id not in concept_ids:
                raise ValueError(f"Relation subject {r.subject_concept_id} not found in concepts")
            if r.object_concept_id not in concept_ids:
                raise ValueError(f"Relation object {r.object_concept_id} not found in concepts")

        # Validate relation duplicates
        rel_signatures = [
            (r.subject_concept_id, r.predicate, r.object_concept_id) for r in self.relations
        ]
        if len(rel_signatures) != len(set(rel_signatures)):
            raise ValueError("Duplicate relations found")

        # Validate evidence alias format
        for c in self.concepts:
            for e in c.evidence:
                if not re.match(r"^E[1-9][0-9]*$", e):
                    raise ValueError(f"Invalid evidence alias format: {e}")
        for r in self.relations:
            for e in r.evidence:
                if not re.match(r"^E[1-9][0-9]*$", e):
                    raise ValueError(f"Invalid evidence alias format: {e}")

        return self


class SemanticConceptV3Stored(BaseModel):
    concept_id: str = Field(pattern=r"^C[1-8]$")
    surface_label: str
    preferred_label: str
    language: str
    entity_types: List[str] = Field(default_factory=list, min_length=1, max_length=3)
    domains: List[str] = Field(default_factory=list, min_length=1, max_length=5)
    context_roles: List[str] = Field(default_factory=list, max_length=3)

    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: List[str] = Field(default_factory=list)

    @field_validator("entity_types", "domains", "context_roles")
    @classmethod
    def validate_snake_case(cls, v):
        return _validate_snake_case_list(v)


class SemanticRelationV3Stored(BaseModel):
    subject_concept_id: str = Field(pattern=r"^C[1-8]$")
    predicate: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    object_concept_id: str = Field(pattern=r"^C[1-8]$")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: List[str] = Field(default_factory=list)


class TaggerOutputV3Stored(BaseModel):
    schema_version: Literal["semantic-tags-v3"] = "semantic-tags-v3"
    languages: List[str] = Field(description="Zidentyfikowane jezyki")
    content_types: List[str]
    unit_quality: Literal["meaningful", "junk"]
    concepts: List[SemanticConceptV3Stored] = Field(default_factory=list, max_length=8)
    relations: List[SemanticRelationV3Stored] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_unit_quality(self):
        if self.unit_quality == "meaningful":
            if not self.concepts:
                raise ValueError("meaningful unit must have at least one concept")
        elif self.unit_quality == "junk":
            if self.concepts or self.relations:
                raise ValueError("junk unit must have empty concepts and relations")
        return self

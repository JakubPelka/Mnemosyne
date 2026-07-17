# Instrukcja

Jesteś zaawansowanym asystentem analizy semantycznej. Twoim zadaniem jest ekstrakcja kluczowych pojęć (concepts) i relacji (relations) z tekstu.
Wyodrębnij tylko to, co faktycznie znajduje się w tekście. 

- Usually return 3 to 6 concepts.
- Eight concepts is an absolute maximum, not a target.
- Do not add weak concepts to fill the limit.
- Relations may be empty.
- Every non-junk concept must have at least one entity_type and one domain.
- Use one to three independent entity types where useful.
- Keep surface labels in the source language.
- Use English ASCII snake_case only for facets and predicates.
- Return compact valid JSON only.
- Never invent evidence aliases or concept IDs.
- Stop adding concepts before risking an incomplete response.

Zwracasz czysty kod JSON zgodny z wklejonym schematem, bez otoczek i zbędnych komentarzy.

## Schema
{schema_json}

## Format i Identyfikatory
Używaj C1, C2, ..., C8 jako identyfikatorów pojęć (concept_id).
Używaj dokładnie tych samych aliasów E1, E2 itp. jako dowodów w evidence.

## Przykłady faset (nie kopiuj, użyj jako inspiracji)

surface_label: fönsterruta
entity_types:
  - vehicle_part
  - physical_object
domains:
  - vehicles
context_roles:
  - damage_location

surface_label: passagerarsidan
entity_types:
  - spatial_location
  - vehicle_region
domains:
  - vehicles
context_roles:
  - damage_location_qualifier

surface_label: Åsa
entity_types:
  - geographic_place
  - locality
domains:
  - geography
context_roles:
  - analysis_area

surface_label: Driven
entity_types:
  - emotion
  - psychological_state
domains:
  - personal_reflection
  - work
context_roles:
  - reported_feeling

surface_label: Gaussian Splatting
entity_types:
  - method
  - computer_graphics_technique
domains:
  - computer_graphics
  - 3d_visualization
context_roles:
  - technology_under_discussion

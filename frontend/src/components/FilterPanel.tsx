import type { FormEvent } from "react";

import type { GraphFilters, MetaResponse } from "../types";

interface FilterPanelProps {
  filters: GraphFilters;
  meta: MetaResponse | null;
  selectedTopicId: string | null;
  onChange: (filters: GraphFilters) => void;
  onApply: () => void;
  onReset: () => void;
}

export function FilterPanel({
  filters,
  meta,
  selectedTopicId,
  onChange,
  onApply,
  onReset,
}: FilterPanelProps) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onApply();
  };
  return (
    <form className="filter-form" onSubmit={submit}>
      <label>
        Widok
        <select
          aria-label="Widok grafu"
          value={filters.graphView}
          onChange={(event) =>
            onChange({
              ...filters,
              graphView: event.target.value as GraphFilters["graphView"],
              category: "",
            })
          }
        >
          <option value="terms">Terminy</option>
          <option value="topics">Tematy (beta)</option>
        </select>
      </label>
      <label>
        Źródło
        <select value={filters.sourceType} onChange={(event) => onChange({ ...filters, sourceType: event.target.value })}>
          <option value="">Wszystkie lokalne</option>
          {meta?.source_types.map((source) => <option value={source} key={source}>{source}</option>)}
        </select>
      </label>
      <label>
        Kategoria
        <select disabled={filters.graphView === "terms"} value={filters.category} onChange={(event) => onChange({ ...filters, category: event.target.value })}>
          <option value="">Wszystkie</option>
          {meta?.topic_categories.map((category) => <option value={category} key={category}>{category}</option>)}
        </select>
      </label>
      <label>
        Minimalna liczba wystąpień <output>{filters.minOccurrences}</output>
        <input type="range" min="1" max="50" value={filters.minOccurrences} onChange={(event) => onChange({ ...filters, minOccurrences: Number(event.target.value) })} />
      </label>
      <label>
        Minimalna siła relacji <output>{filters.minRelationWeight.toFixed(2)}</output>
        <input type="range" min="0" max="1" step="0.01" value={filters.minRelationWeight} onChange={(event) => onChange({ ...filters, minRelationWeight: Number(event.target.value) })} />
      </label>
      <label>
        Limit węzłów
        <select value={filters.nodeLimit} onChange={(event) => onChange({ ...filters, nodeLimit: Number(event.target.value) })}>
          {[20, 30, 50, 100, 200, 400].map((limit) => <option value={limit} key={limit}>{limit}</option>)}
        </select>
      </label>
      <label className="toggle">
        <input type="checkbox" checked={filters.neighborsOnly} disabled={!selectedTopicId} onChange={(event) => onChange({ ...filters, neighborsOnly: event.target.checked })} />
        Tylko sąsiedzi wybranego węzła
      </label>
      <div className="filter-actions">
        <button className="primary" type="submit">Zastosuj</button>
        <button type="button" onClick={onReset}>Resetuj</button>
      </div>
    </form>
  );
}

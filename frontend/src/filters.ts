import type { GraphFilters } from "./types";

export const DEFAULT_FILTERS: GraphFilters = {
  graphView: "terms",
  startMonth: "",
  endMonth: "",
  sourceType: "",
  category: "",
  minOccurrences: 2,
  minRelationWeight: 0.15,
  nodeLimit: 30,
  neighborsOnly: false,
};

export function monthFromDate(value: string | null): string {
  return value ? value.slice(0, 7) : "";
}

export function startOfMonth(value: string): string {
  return `${value}-01T00:00:00.000Z`;
}

export function startOfNextMonth(value: string): string {
  const [year, month] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month, 1)).toISOString();
}

export function graphQuery(filters: GraphFilters, selectedTopicId: string | null): string {
  const params = new URLSearchParams({
    min_occurrences: String(filters.minOccurrences),
    min_relation_weight: String(filters.minRelationWeight),
    node_limit: String(filters.nodeLimit),
    privacy_level: "private",
    layer: filters.graphView,
  });
  if (filters.startMonth) params.set("start", startOfMonth(filters.startMonth));
  if (filters.endMonth) params.set("end", startOfNextMonth(filters.endMonth));
  if (filters.sourceType) params.set("source_type", filters.sourceType);
  if (filters.category) params.append("category", filters.category);
  if (selectedTopicId) params.set("selected_topic_id", selectedTopicId);
  if (filters.neighborsOnly && selectedTopicId) params.set("neighbors_only", "true");
  return params.toString();
}

export function occurrenceQuery(filters: GraphFilters, limit: number, offset: number): string {
  const params = new URLSearchParams({
    privacy_level: "private",
    limit: String(limit),
    offset: String(offset),
  });
  if (filters.startMonth) params.set("start", startOfMonth(filters.startMonth));
  if (filters.endMonth) params.set("end", startOfNextMonth(filters.endMonth));
  if (filters.sourceType) params.set("source_type", filters.sourceType);
  return params.toString();
}

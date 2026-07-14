import { graphQuery, occurrenceQuery } from "./filters";
import type {
  EventExcerptPage,
  ExploreResult,
  GraphFilters,
  GraphResponse,
  MessageContext,
  MetaResponse,
  TopicDetail,
  TopicSearchItem,
} from "./types";

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${response.statusText || "request failed"}`);
  }
  return (await response.json()) as T;
}

export const api = {
  meta: (signal?: AbortSignal) => request<MetaResponse>("/api/meta", signal),
  graph: (filters: GraphFilters, selectedTopicId: string | null, signal?: AbortSignal) =>
    request<GraphResponse>(`/api/graph?${graphQuery(filters, selectedTopicId)}`, signal),
  searchTopics: (query: string, signal?: AbortSignal) =>
    request<{ items: TopicSearchItem[] }>(
      `/api/topics/search?q=${encodeURIComponent(query)}&limit=12&privacy_level=private&layer=all`,
      signal,
    ),
  searchEvents: (
    query: string,
    contentScope: "all" | "prose" | "code" | "commands" | "logs",
    signal?: AbortSignal,
  ) =>
    request<EventExcerptPage>(
      `/api/search/events?q=${encodeURIComponent(query)}&limit=12&offset=0&privacy_level=private&content_scope=${contentScope}`,
      signal,
    ),
  explore: (
    query: string,
    filters: GraphFilters,
    limit: number,
    offset: number,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams(occurrenceQuery(filters, limit, offset));
    params.set("q", query);
    params.set("content_scope", "prose");
    return request<ExploreResult>(`/api/search/explore?${params}`, signal);
  },
  topic: (topicId: string, layer: GraphFilters["graphView"], signal?: AbortSignal) =>
    request<TopicDetail>(
      `/api/topics/${encodeURIComponent(topicId)}?privacy_level=private&layer=${layer}`,
      signal,
    ),
  occurrences: (
    topicId: string,
    filters: GraphFilters,
    limit: number,
    offset: number,
    signal?: AbortSignal,
  ) =>
    request<EventExcerptPage>(
      `/api/topics/${encodeURIComponent(topicId)}/occurrences?${occurrenceQuery(filters, limit, offset)}&layer=${filters.graphView}`,
      signal,
    ),
  context: (eventId: string, signal?: AbortSignal) =>
    request<MessageContext>(
      `/api/messages/${encodeURIComponent(eventId)}/context?before=2&after=2`,
      signal,
    ),
};

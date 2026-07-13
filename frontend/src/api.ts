import { graphQuery, occurrenceQuery } from "./filters";
import type {
  EventExcerptPage,
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
      `/api/topics/search?q=${encodeURIComponent(query)}&limit=12&privacy_level=private`,
      signal,
    ),
  topic: (topicId: string, signal?: AbortSignal) =>
    request<TopicDetail>(
      `/api/topics/${encodeURIComponent(topicId)}?privacy_level=private`,
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
      `/api/topics/${encodeURIComponent(topicId)}/occurrences?${occurrenceQuery(filters, limit, offset)}`,
      signal,
    ),
  context: (eventId: string, signal?: AbortSignal) =>
    request<MessageContext>(
      `/api/messages/${encodeURIComponent(eventId)}/context?before=2&after=2`,
      signal,
    ),
};

import type { EventExcerptPage, GraphResponse, TopicDetail, TopicSearchItem } from "../src/types";

export const topic: TopicSearchItem = {
  topic_id: "topic-alpha",
  name: "alpha",
  category: "keyword",
  message_count: 9,
  context_count: 3,
  first_seen_at: "2024-01-01T00:00:00Z",
  last_seen_at: "2024-03-01T00:00:00Z",
  layer: "terms",
};

export const graphResponse: GraphResponse = {
  nodes: [topic, { ...topic, topic_id: "topic-beta", name: "beta", message_count: 4 }],
  edges: [{ source_topic_id: "topic-alpha", target_topic_id: "topic-beta", message_count: 3, context_count: 2, weight: 0.5 }],
};

export const detail: TopicDetail = {
  ...topic,
  months: [{ month: "2024-01", message_count: 3, weight: 1.2 }],
  neighbors: [{ topic_id: "topic-beta", name: "beta", category: "keyword", message_count: 3, context_count: 2, weight: 0.5 }],
};

export const occurrences: EventExcerptPage = {
  total: 1,
  limit: 12,
  offset: 0,
  items: [{ event_id: "event-one", occurred_at: null, role: "user", conversation_title: "Synthetic context", snippet: "Synthetic excerpt", source_record_id: "stable-one", match_type: "code" }],
};

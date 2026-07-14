export interface MetaResponse {
  earliest_event_at: string | null;
  latest_event_at: string | null;
  source_types: string[];
  topic_categories: string[];
  counts: { events: number; topics: number; relations: number };
  api_version: string;
}

export interface GraphNode {
  topic_id: string;
  name: string;
  category: string;
  message_count: number;
  context_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface GraphEdge {
  source_topic_id: string;
  target_topic_id: string;
  message_count: number;
  context_count: number;
  weight: number;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface MonthlyIntensity {
  month: string;
  message_count: number;
  weight: number;
}

export interface TopicSearchItem extends GraphNode {
  layer: "terms" | "topics";
}

export interface TopicNeighbor {
  topic_id: string;
  name: string;
  category: string;
  message_count: number;
  context_count: number;
  weight: number;
}

export interface TopicDetail extends GraphNode {
  layer: "terms" | "topics";
  months: MonthlyIntensity[];
  neighbors: TopicNeighbor[];
}

export interface EventExcerpt {
  event_id: string;
  occurred_at: string | null;
  role: string | null;
  conversation_title: string | null;
  snippet: string;
  source_record_id: string;
  match_type: "prose" | "code" | "inline_code" | "shell_command" | "log" | "quote" | "table" | "link" | "unknown" | null;
}

export interface EventExcerptPage {
  items: EventExcerpt[];
  total: number;
  limit: number;
  offset: number;
}

export interface ContextMessage {
  event_id: string;
  message_id: string;
  role: string;
  created_at: string | null;
  text: string | null;
  sequence_number: number;
  is_target: boolean;
}

export interface MessageContext {
  conversation_id: string;
  conversation_title: string | null;
  messages: ContextMessage[];
}

export interface GraphFilters {
  graphView: "topics" | "terms";
  startMonth: string;
  endMonth: string;
  sourceType: string;
  category: string;
  minOccurrences: number;
  minRelationWeight: number;
  nodeLimit: number;
  neighborsOnly: boolean;
}

import { aggregateNodeId, exploreGraph, toGraphology } from "../src/graph";
import { graphResponse } from "./fixtures";

test("maps API graph to Graphology with transformed sizes", () => {
  const graph = toGraphology(graphResponse, "topic-alpha");
  expect(graph.order).toBe(2);
  expect(graph.size).toBe(1);
  expect(graph.getNodeAttribute("topic-alpha", "size")).toBeGreaterThan(graph.getNodeAttribute("topic-beta", "size"));
  expect(graph.getNodeAttribute("topic-alpha", "highlighted")).toBe(true);
  expect(graph.getEdgeAttribute(graph.edges()[0], "size")).toBeGreaterThan(0);
});

test("builds a local aggregate graph around a virtual query node", () => {
  const response = exploreGraph({
    query: "synthetic place",
    normalized_query: "synthetic place",
    matched_terms: [{ item_id: "term-place", name: "synthetic place portal", layer: "terms", message_count: 4, context_count: 2 }],
    matched_topics: [],
    unique_event_count: 5,
    unique_context_count: 3,
    first_seen_at: null,
    last_seen_at: null,
    monthly_intensity: [],
    neighbors: [{ topic_id: "term-maps", name: "spatial maps", category: "term", message_count: 3, context_count: 2, weight: 0.4 }],
    occurrences: { items: [], total: 0, limit: 12, offset: 0 },
  });
  const queryId = aggregateNodeId("synthetic place");
  expect(response.nodes.length).toBeGreaterThan(0);
  expect(response.nodes[0]).toMatchObject({ topic_id: queryId, category: "aggregate_query" });
  expect(response.edges.some((edge) => edge.source_topic_id === queryId && edge.target_topic_id === "term-maps")).toBe(true);
});

test("keeps the virtual query node when aggregate neighbors are empty", () => {
  const response = exploreGraph({
    query: "unmatched concept",
    normalized_query: "unmatched concept",
    matched_terms: [], matched_topics: [], unique_event_count: 0, unique_context_count: 0,
    first_seen_at: null, last_seen_at: null, monthly_intensity: [], neighbors: [],
    occurrences: { items: [], total: 0, limit: 12, offset: 0 },
  });
  expect(response.nodes).toHaveLength(1);
  expect(response.edges).toHaveLength(0);
});

test("reuses cached positions across filtered graph responses", () => {
  const cache = new Map();
  const first = toGraphology(graphResponse, null, cache);
  const position = first.getNodeAttributes("topic-alpha");
  const second = toGraphology({ nodes: [graphResponse.nodes[0]], edges: [] }, null, cache);
  expect(second.getNodeAttribute("topic-alpha", "x")).toBe(position.x);
  expect(second.getNodeAttribute("topic-alpha", "y")).toBe(position.y);
});

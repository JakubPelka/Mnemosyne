import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";

import type { ExploreResult, GraphResponse } from "./types";

export interface Position {
  x: number;
  y: number;
}

const CATEGORY_COLORS = ["#68d8c8", "#f3b562", "#8aa8ff", "#e58cba", "#b9d96c"];

export function aggregateNodeId(normalizedQuery: string): string {
  return `query:${normalizedQuery}`;
}

export function exploreGraph(result: ExploreResult): GraphResponse {
  const queryId = aggregateNodeId(result.normalized_query);
  const nodes = new Map();
  for (const neighbor of result.neighbors) {
    if (!nodes.has(neighbor.topic_id)) {
      nodes.set(neighbor.topic_id, {
        topic_id: neighbor.topic_id,
        name: neighbor.name,
        category: neighbor.category,
        message_count: neighbor.message_count,
        context_count: neighbor.context_count,
        first_seen_at: result.first_seen_at,
        last_seen_at: result.last_seen_at,
      });
    }
  }
  const relatedNodes = [...nodes.values()];
  return {
    nodes: [{
      topic_id: queryId,
      name: result.query,
      category: "aggregate_query",
      message_count: result.unique_event_count,
      context_count: result.unique_context_count,
      first_seen_at: result.first_seen_at,
      last_seen_at: result.last_seen_at,
    }, ...relatedNodes],
    edges: relatedNodes.map((node) => {
      const neighbor = result.neighbors.find((item) => item.topic_id === node.topic_id);
      return {
        source_topic_id: queryId,
        target_topic_id: node.topic_id,
        message_count: neighbor?.message_count ?? node.message_count,
        context_count: neighbor?.context_count ?? node.context_count,
        weight: neighbor?.weight ?? 1,
      };
    }),
  };
}

export function toGraphology(
  response: GraphResponse,
  selectedTopicId: string | null,
  positionCache: Map<string, Position> = new Map(),
): Graph {
  const graph = new Graph({ type: "undirected", multi: false });
  const importantThreshold = [...response.nodes]
    .sort((a, b) => b.message_count - a.message_count)
    .at(Math.min(14, Math.max(response.nodes.length - 1, 0)))?.message_count;

  for (const node of response.nodes) {
    const position = positionCache.get(node.topic_id) ?? deterministicPosition(node.topic_id);
    graph.addNode(node.topic_id, {
      x: position.x,
      y: position.y,
      size: Math.min(13, 3 + Math.log2(node.message_count + 1) * 0.85),
      label:
        node.topic_id === selectedTopicId || node.message_count >= (importantThreshold ?? Infinity)
          ? node.name
          : "",
      color: node.category === "aggregate_query" ? "#fff2a8" : node.topic_id === selectedTopicId ? "#fff2a8" : categoryColor(node.category),
      highlighted: node.topic_id === selectedTopicId,
      forceLabel: node.topic_id === selectedTopicId,
      category: node.category,
      messageCount: node.message_count,
    });
  }
  for (const edge of response.edges) {
    if (!graph.hasNode(edge.source_topic_id) || !graph.hasNode(edge.target_topic_id)) continue;
    graph.addEdge(edge.source_topic_id, edge.target_topic_id, {
      size: Math.min(2.2, 0.2 + Math.sqrt(edge.weight) * 2),
      color: "#294a45",
      weight: edge.weight,
    });
  }

  const uncached = response.nodes.some((node) => !positionCache.has(node.topic_id));
  if (uncached && graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations: graph.order > 300 ? 15 : Math.min(100, 25 + Math.ceil(graph.order / 2)),
      settings: {
        ...forceAtlas2.inferSettings(graph),
        gravity: 0.08,
        scalingRatio: 12,
        slowDown: 8,
        adjustSizes: true,
        barnesHutOptimize: graph.order > 100,
      },
    });
  }
  graph.forEachNode((key, attributes) => {
    positionCache.set(key, { x: attributes.x as number, y: attributes.y as number });
  });
  return graph;
}

function deterministicPosition(key: string): Position {
  let hash = 2166136261;
  for (let index = 0; index < key.length; index += 1) {
    hash ^= key.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const unsigned = hash >>> 0;
  const angle = (unsigned / 0xffffffff) * Math.PI * 2;
  const radius = 1 + ((unsigned >>> 8) % 1000) / 400;
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

function categoryColor(category: string): string {
  let hash = 0;
  for (const character of category) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return CATEGORY_COLORS[hash % CATEGORY_COLORS.length];
}

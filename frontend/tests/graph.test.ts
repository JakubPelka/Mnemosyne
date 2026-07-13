import { toGraphology } from "../src/graph";
import { graphResponse } from "./fixtures";

test("maps API graph to Graphology with transformed sizes", () => {
  const graph = toGraphology(graphResponse, "topic-alpha");
  expect(graph.order).toBe(2);
  expect(graph.size).toBe(1);
  expect(graph.getNodeAttribute("topic-alpha", "size")).toBeGreaterThan(graph.getNodeAttribute("topic-beta", "size"));
  expect(graph.getNodeAttribute("topic-alpha", "highlighted")).toBe(true);
  expect(graph.getEdgeAttribute(graph.edges()[0], "size")).toBeGreaterThan(0);
});

test("reuses cached positions across filtered graph responses", () => {
  const cache = new Map();
  const first = toGraphology(graphResponse, null, cache);
  const position = first.getNodeAttributes("topic-alpha");
  const second = toGraphology({ nodes: [graphResponse.nodes[0]], edges: [] }, null, cache);
  expect(second.getNodeAttribute("topic-alpha", "x")).toBe(position.x);
  expect(second.getNodeAttribute("topic-alpha", "y")).toBe(position.y);
});

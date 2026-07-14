import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../src/components/GraphCanvas", () => ({
  GraphCanvas: () => <div data-testid="synthetic-graph" />,
}));

import App from "../src/App";

test("Enter explores all matches and changing the query clears the result", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/meta") {
      return new Response(JSON.stringify({
        earliest_event_at: "2024-01-01T00:00:00Z",
        latest_event_at: "2024-02-01T00:00:00Z",
        source_types: ["chatgpt"],
        topic_categories: [],
        counts: { events: 4, candidate_terms: 3, topics: 1, candidate_term_relations: 2, topic_relations: 0 },
        api_version: "0.5.0",
      }), { status: 200 });
    }
    if (url.startsWith("/api/graph?")) {
      return new Response(JSON.stringify({ nodes: [], edges: [] }), { status: 200 });
    }
    if (url.startsWith("/api/search/explore?")) {
      return new Response(JSON.stringify({
        query: "synthetic place",
        normalized_query: "synthetic place",
        matched_terms: [],
        matched_topics: [],
        unique_event_count: 2,
        unique_context_count: 1,
        first_seen_at: null,
        last_seen_at: null,
        monthly_intensity: [],
        neighbors: [],
        occurrences: { items: [], total: 0, limit: 12, offset: 0 },
      }), { status: 200 });
    }
    if (url.startsWith("/api/topics/search?")) {
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    }
    throw new Error(`Unexpected local request: ${url}`);
  });

  render(<App />);
  const input = screen.getByLabelText("Wyszukaj termin lub temat");
  fireEvent.change(input, { target: { value: "synthetic place" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await screen.findByText("WYNIK ZBIORCZY")).toBeInTheDocument();
  expect(screen.getByText("unikalnych wiadomości", { exact: false }).closest("span")).toHaveTextContent("2 unikalnych wiadomości");
  expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/search/explore?"))).toBe(true);

  fireEvent.change(input, { target: { value: "different query" } });
  await waitFor(() => expect(screen.queryByText("WYNIK ZBIORCZY")).not.toBeInTheDocument());
  expect(screen.getByText(/wybierz węzeł/i)).toBeInTheDocument();
});

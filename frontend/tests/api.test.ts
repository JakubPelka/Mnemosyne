import { api } from "../src/api";
import { DEFAULT_FILTERS } from "../src/filters";

test("API client maps graph filters to local endpoint", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ nodes: [], edges: [] }), { status: 200 }),
  );
  await api.graph({ ...DEFAULT_FILTERS, startMonth: "2024-01", endMonth: "2024-03", sourceType: "chatgpt" }, "topic-one");
  const url = String(fetchMock.mock.calls[0][0]);
  expect(url).toContain("/api/graph?");
  expect(url).toContain("start=2024-01-01");
  expect(url).toContain("end=2024-04-01");
  expect(url).toContain("source_type=chatgpt");
  expect(url).toContain("privacy_level=private");
  expect(url).toContain("layer=terms");
  expect(url).toContain("selected_topic_id=topic-one");
});

test("API client reports backend failures", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 503, statusText: "Offline" }));
  await expect(api.meta()).rejects.toThrow("API 503: Offline");
});

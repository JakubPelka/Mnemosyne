import { DEFAULT_FILTERS, graphQuery, occurrenceQuery } from "../src/filters";

test("graph filters omit neighbors without a selected topic", () => {
  const query = graphQuery({ ...DEFAULT_FILTERS, neighborsOnly: true, category: "keyword" }, null);
  expect(query).toContain("category=keyword");
  expect(query).not.toContain("neighbors_only");
});

test("occurrence filters preserve paging", () => {
  const query = occurrenceQuery({ ...DEFAULT_FILTERS, sourceType: "chatgpt" }, 12, 24);
  expect(query).toContain("limit=12");
  expect(query).toContain("offset=24");
  expect(query).toContain("source_type=chatgpt");
});

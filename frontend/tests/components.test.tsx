import { fireEvent, render, screen } from "@testing-library/react";

import { FilterPanel } from "../src/components/FilterPanel";
import { StatusPanel } from "../src/components/StatusPanel";
import { TopicDetails } from "../src/components/TopicDetails";
import { TopicSearchResults } from "../src/components/TopicSearchResults";
import { DEFAULT_FILTERS } from "../src/filters";
import { detail, occurrences, topic } from "./fixtures";

test("selects a topic from search results", () => {
  const onSelect = vi.fn();
  render(<TopicSearchResults query="alp" results={[topic]} onSelect={onSelect} />);
  fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
  expect(onSelect).toHaveBeenCalledWith("topic-alpha");
});

test("renders empty and backend error states", () => {
  const { rerender } = render(<StatusPanel empty />);
  expect(screen.getByText(/brak tematów/i)).toBeInTheDocument();
  rerender(<StatusPanel error="API 503" />);
  expect(screen.getByRole("alert")).toHaveTextContent("API 503");
});

test("loads limited context after choosing an excerpt", () => {
  const onLoadContext = vi.fn();
  const { rerender } = render(
    <TopicDetails detail={detail} occurrences={occurrences} context={null} loading={false} error={null} onSelectNeighbor={vi.fn()} onLoadContext={onLoadContext} onPage={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: /synthetic context/i }));
  expect(onLoadContext).toHaveBeenCalledWith("event-one");

  rerender(
    <TopicDetails detail={detail} occurrences={occurrences} context={{ conversation_id: "context-one", conversation_title: "Synthetic context", messages: [{ event_id: "event-one", message_id: "message-one", role: "user", created_at: null, text: "Synthetic target", sequence_number: 1, is_target: true }] }} loading={false} error={null} onSelectNeighbor={vi.fn()} onLoadContext={onLoadContext} onPage={vi.fn()} />,
  );
  expect(screen.getByLabelText(/ograniczony kontekst/i)).toHaveTextContent("Synthetic target");
  expect(screen.getByText("Synthetic target").closest("article")).toHaveClass("target");
});

test("switches to the diagnostic raw-term view", () => {
  const onChange = vi.fn();
  render(
    <FilterPanel
      filters={DEFAULT_FILTERS}
      meta={null}
      selectedTopicId={null}
      onChange={onChange}
      onApply={vi.fn()}
      onReset={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("Widok grafu"), { target: { value: "terms" } });
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ graphView: "terms" }));
});

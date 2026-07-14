import type { TopicSearchItem } from "../types";

interface TopicSearchResultsProps {
  query: string;
  results: TopicSearchItem[];
  onSelect: (topicId: string, layer: "terms" | "topics") => void;
}

export function TopicSearchResults({ query, results, onSelect }: TopicSearchResultsProps) {
  if (!query.trim()) return null;
  if (!results.length) return <p className="muted">Brak pasujących terminów lub tematów.</p>;
  return (
    <ul className="search-results" aria-label="Wyniki wyszukiwania terminów i tematów">
      {results.map((topic) => (
        <li key={`${topic.layer}:${topic.topic_id}`}>
          <button type="button" onClick={() => onSelect(topic.topic_id, topic.layer)}>
            <span>{topic.name}</span>
            <small>{topic.layer === "topics" ? "temat beta" : "termin"} · {topic.message_count} wiadomości</small>
          </button>
        </li>
      ))}
    </ul>
  );
}

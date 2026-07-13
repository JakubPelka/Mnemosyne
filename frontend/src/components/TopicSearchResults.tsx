import type { TopicSearchItem } from "../types";

interface TopicSearchResultsProps {
  query: string;
  results: TopicSearchItem[];
  onSelect: (topicId: string) => void;
}

export function TopicSearchResults({ query, results, onSelect }: TopicSearchResultsProps) {
  if (!query.trim()) return null;
  if (!results.length) return <p className="muted">Brak pasujących tematów.</p>;
  return (
    <ul className="search-results" aria-label="Wyniki wyszukiwania tematów">
      {results.map((topic) => (
        <li key={topic.topic_id}>
          <button type="button" onClick={() => onSelect(topic.topic_id)}>
            <span>{topic.name}</span>
            <small>{topic.message_count} wiadomości</small>
          </button>
        </li>
      ))}
    </ul>
  );
}

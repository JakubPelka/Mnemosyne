import type { TopicSearchItem } from "../types";

interface TopicSearchResultsProps {
  query: string;
  results: TopicSearchItem[];
  onExplore: (query: string) => void;
  onSelect: (topicId: string, layer: "terms" | "topics") => void;
}

export function TopicSearchResults({ query, results, onExplore, onSelect }: TopicSearchResultsProps) {
  if (!query.trim()) return null;
  return (
    <>
      <button className="explore-result" type="button" onClick={() => onExplore(query.trim())}>
        <span>Eksploruj „{query.trim()}”</span>
        <small>wszystkie dopasowania</small>
      </button>
      {results.length ? (
        <ul className="search-results" aria-label="Dopasowane terminy i tematy">
          {results.map((topic) => (
            <li key={`${topic.layer}:${topic.topic_id}`}>
              <button type="button" onClick={() => onSelect(topic.topic_id, topic.layer)}>
                <span>{topic.name}</span>
                <small>{topic.layer === "topics" ? "Temat" : "Termin"} · {topic.message_count} wiadomości</small>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="muted">Brak dokładnych rekordów — nadal możesz eksplorować treść.</p>}
    </>
  );
}

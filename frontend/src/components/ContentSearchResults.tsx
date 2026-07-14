import type { EventExcerpt } from "../types";

interface ContentSearchResultsProps {
  query: string;
  results: EventExcerpt[];
  onSelect: (eventId: string) => void;
}

const LABELS = {
  prose: "PROZA",
  code: "KOD",
  inline_code: "KOD INLINE",
  shell_command: "KOMENDA",
  log: "LOG",
  quote: "CYTAT",
  table: "TABELA",
  link: "LINK",
  unknown: "TREŚĆ",
} as const;

export function ContentSearchResults({ query, results, onSelect }: ContentSearchResultsProps) {
  if (!query.trim()) return null;
  if (!results.length) return <p className="muted">Brak pasujących fragmentów.</p>;
  return (
    <ul className="search-results" aria-label="Wyniki wyszukiwania treści">
      {results.map((item, index) => (
        <li key={`${item.event_id}:${item.match_type}:${index}`}>
          <button type="button" onClick={() => onSelect(item.event_id)}>
            <span>{item.snippet || "Brak tekstu"}</span>
            <small>{item.match_type ? LABELS[item.match_type] : "TREŚĆ"}</small>
          </button>
        </li>
      ))}
    </ul>
  );
}

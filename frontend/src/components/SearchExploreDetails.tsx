import type { ExploreResult, MessageContext } from "../types";
import { ContextView, EventExcerptList, IntensityChart, formatDate } from "./TopicDetails";

interface Props {
  result: ExploreResult;
  context: MessageContext | null;
  loading: boolean;
  error: string | null;
  onSelect: (id: string, layer: "terms" | "topics") => void;
  onLoadContext: (eventId: string) => void;
  onPage: (offset: number) => void;
}

export function SearchExploreDetails({ result, context, loading, error, onSelect, onLoadContext, onPage }: Props) {
  if (loading) return <div className="detail-placeholder">Ładowanie wyniku zbiorczego…</div>;
  if (error) return <div className="detail-placeholder error" role="alert">{error}</div>;
  const matches = [...result.matched_topics, ...result.matched_terms];
  return (
    <div className="topic-detail">
      <header>
        <span className="eyebrow">WYNIK ZBIORCZY</span>
        <h2>{result.query}</h2>
        <div className="stat-grid">
          <span><strong>{result.unique_event_count}</strong> unikalnych wiadomości</span>
          <span><strong>{result.unique_context_count}</strong> kontekstów</span>
        </div>
        <p className="date-span">{formatDate(result.first_seen_at)} — {formatDate(result.last_seen_at)}</p>
      </header>
      <section><h3>Intensywność miesięczna</h3><IntensityChart months={result.monthly_intensity} /></section>
      <section>
        <h3>Dopasowane terminy i tematy</h3>
        {matches.length ? <div className="neighbor-list">{matches.map((item) => (
          <button key={`${item.layer}:${item.item_id}`} type="button" onClick={() => onSelect(item.item_id, item.layer)}>
            <span>{item.name}</span><small>{item.layer === "topics" ? "Temat" : "Termin"}</small>
          </button>
        ))}</div> : <p className="muted">Dopasowanie pochodzi wyłącznie z treści prozy.</p>}
      </section>
      <section>
        <h3>Sąsiednie pojęcia</h3>
        {result.neighbors.length ? <div className="neighbor-list">{result.neighbors.map((item) => (
          <button key={item.topic_id} type="button" onClick={() => onSelect(item.topic_id, "terms")}>
            <span>{item.name}</span><small>{Math.round(item.weight * 100)}%</small>
          </button>
        ))}</div> : <p className="muted">Brak wiarygodnych sąsiadów.</p>}
      </section>
      <section><h3>Źródłowe fragmenty</h3><EventExcerptList occurrences={result.occurrences} onLoadContext={onLoadContext} onPage={onPage} /></section>
      {context && <ContextView context={context} />}
    </div>
  );
}

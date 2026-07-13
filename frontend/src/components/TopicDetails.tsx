import type { EventExcerptPage, MessageContext, TopicDetail } from "../types";

interface TopicDetailsProps {
  detail: TopicDetail | null;
  occurrences: EventExcerptPage | null;
  context: MessageContext | null;
  loading: boolean;
  error: string | null;
  onSelectNeighbor: (topicId: string) => void;
  onLoadContext: (eventId: string) => void;
  onPage: (offset: number) => void;
}

export function TopicDetails({
  detail,
  occurrences,
  context,
  loading,
  error,
  onSelectNeighbor,
  onLoadContext,
  onPage,
}: TopicDetailsProps) {
  if (loading) return <div className="detail-placeholder">Ładowanie szczegółów…</div>;
  if (error)
    return (
      <div className="detail-placeholder error" role="alert">
        {error}
      </div>
    );
  if (!detail)
    return <div className="detail-placeholder">Wybierz węzeł, aby zobaczyć jego historię.</div>;

  const maxIntensity = Math.max(1, ...detail.months.map((month) => month.message_count));
  return (
    <div className="topic-detail">
      <header>
        <span className="eyebrow">{detail.category}</span>
        <h2>{detail.name}</h2>
        <div className="stat-grid">
          <span><strong>{detail.message_count}</strong> wiadomości</span>
          <span><strong>{detail.context_count}</strong> kontekstów</span>
        </div>
        <p className="date-span">{formatDate(detail.first_seen_at)} — {formatDate(detail.last_seen_at)}</p>
      </header>

      <section>
        <h3>Intensywność miesięczna</h3>
        {detail.months.length ? (
          <div className="intensity-chart" aria-label="Miesięczna intensywność tematu">
            {detail.months.map((month) => (
              <div className="intensity-column" key={month.month} title={`${month.month}: ${month.message_count}`}>
                <span style={{ height: `${Math.max(5, (month.message_count / maxIntensity) * 100)}%` }} />
                <small>{month.month.slice(5)}</small>
              </div>
            ))}
          </div>
        ) : <p className="muted">Brak zdarzeń z datą.</p>}
      </section>

      <section>
        <h3>Najbliższe tematy</h3>
        {detail.neighbors.length ? (
          <div className="neighbor-list">
            {detail.neighbors.map((neighbor) => (
              <button key={neighbor.topic_id} type="button" onClick={() => onSelectNeighbor(neighbor.topic_id)}>
                <span>{neighbor.name}</span><small>{Math.round(neighbor.weight * 100)}%</small>
              </button>
            ))}
          </div>
        ) : <p className="muted">Brak relacji dla bieżących danych.</p>}
      </section>

      <section>
        <h3>Źródłowe fragmenty</h3>
        {!occurrences?.items.length ? <p className="muted">Temat nie ma fragmentów w tym okresie.</p> : (
          <div className="occurrences">
            {occurrences.items.map((item) => (
              <button key={item.event_id} type="button" onClick={() => onLoadContext(item.event_id)}>
                <span className="occurrence-meta">{item.role ?? "zdarzenie"} · {formatDate(item.occurred_at)}</span>
                <strong>{item.conversation_title ?? "Bez tytułu"}</strong>
                <span className="excerpt">{item.snippet || "Brak tekstu"}</span>
                <code className="source-id" title={item.source_record_id}>{item.source_record_id}</code>
              </button>
            ))}
            <div className="pagination">
              <button type="button" disabled={occurrences.offset === 0} onClick={() => onPage(Math.max(0, occurrences.offset - occurrences.limit))}>Wstecz</button>
              <span>{occurrences.offset + 1}–{Math.min(occurrences.total, occurrences.offset + occurrences.items.length)} z {occurrences.total}</span>
              <button type="button" disabled={occurrences.offset + occurrences.limit >= occurrences.total} onClick={() => onPage(occurrences.offset + occurrences.limit)}>Dalej</button>
            </div>
          </div>
        )}
      </section>

      {context && (
        <section className="context-panel" aria-label="Ograniczony kontekst rozmowy">
          <h3>{context.conversation_title ?? "Kontekst fragmentu"}</h3>
          {context.messages.map((message) => (
            <article key={message.event_id} className={message.is_target ? "target" : ""}>
              <span>{message.role} · {formatDate(message.created_at)}</span>
              <p>{message.text || "Brak tekstu"}</p>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}

function formatDate(value: string | null): string {
  if (!value) return "bez daty";
  return new Intl.DateTimeFormat("pl-PL", { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

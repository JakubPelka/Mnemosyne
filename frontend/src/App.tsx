import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { DEFAULT_FILTERS, monthFromDate } from "./filters";
import { FilterPanel } from "./components/FilterPanel";
import { ContentSearchResults } from "./components/ContentSearchResults";
import { GraphCanvas } from "./components/GraphCanvas";
import { StatusPanel } from "./components/StatusPanel";
import { TopicDetails } from "./components/TopicDetails";
import { TopicSearchResults } from "./components/TopicSearchResults";
import type {
  EventExcerptPage,
  GraphFilters,
  GraphResponse,
  MessageContext,
  MetaResponse,
  TopicDetail,
  TopicSearchItem,
} from "./types";

const OCCURRENCE_LIMIT = 12;

export default function App() {
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [draftFilters, setDraftFilters] = useState<GraphFilters>(DEFAULT_FILTERS);
  const [filters, setFilters] = useState<GraphFilters>(DEFAULT_FILTERS);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TopicDetail | null>(null);
  const [occurrences, setOccurrences] = useState<EventExcerptPage | null>(null);
  const [context, setContext] = useState<MessageContext | null>(null);
  const [occurrenceOffset, setOccurrenceOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<TopicSearchItem[]>([]);
  const [contentSearch, setContentSearch] = useState("");
  const [contentScope, setContentScope] = useState<"all" | "prose" | "code" | "commands" | "logs">("all");
  const [contentResults, setContentResults] = useState<EventExcerptPage["items"]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.meta(controller.signal)
      .then((value) => {
        setMeta(value);
        const initial = {
          ...DEFAULT_FILTERS,
          startMonth: monthFromDate(value.earliest_event_at),
          endMonth: monthFromDate(value.latest_event_at),
          sourceType: value.source_types.length === 1 ? value.source_types[0] : "",
        };
        setDraftFilters(initial);
        setFilters(initial);
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api.graph(filters, selectedTopicId, controller.signal)
      .then(setGraph)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [filters, selectedTopicId]);

  useEffect(() => {
    if (!selectedTopicId) {
      setDetail(null);
      setOccurrences(null);
      setContext(null);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    setDetailError(null);
    Promise.all([
      api.topic(selectedTopicId, filters.graphView, controller.signal),
      api.occurrences(
        selectedTopicId,
        filters,
        OCCURRENCE_LIMIT,
        occurrenceOffset,
        controller.signal,
      ),
    ])
      .then(([topic, page]) => {
        setDetail(topic);
        setOccurrences(page);
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setDetailError(reason.message);
      })
      .finally(() => setDetailLoading(false));
    return () => controller.abort();
  }, [filters, occurrenceOffset, selectedTopicId]);

  useEffect(() => {
    if (!search.trim()) {
      setSearchResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.searchTopics(search.trim(), controller.signal)
        .then((response) => setSearchResults(response.items))
        .catch((reason: Error) => {
          if (reason.name !== "AbortError") setDetailError(reason.message);
        });
    }, 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search]);

  useEffect(() => {
    if (!contentSearch.trim()) {
      setContentResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.searchEvents(contentSearch.trim(), contentScope, controller.signal)
        .then((response) => setContentResults(response.items))
        .catch((reason: Error) => {
          if (reason.name !== "AbortError") setDetailError(reason.message);
        });
    }, 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [contentScope, contentSearch]);

  const selectResult = useCallback((topicId: string, layer: "terms" | "topics") => {
    setDraftFilters((value) => ({ ...value, graphView: layer }));
    setFilters((value) => ({ ...value, graphView: layer }));
    setSelectedTopicId(topicId);
    setOccurrenceOffset(0);
    setContext(null);
    setSearch("");
    setSearchResults([]);
  }, []);

  const selectGraphNode = useCallback(
    (nodeId: string) => {
      setSelectedTopicId(nodeId);
      setOccurrenceOffset(0);
      setContext(null);
      if (filters.graphView === "topics") {
        setSearch("");
        setSearchResults([]);
      }
    },
    [filters.graphView],
  );

  const loadContext = useCallback((eventId: string) => {
    setDetailError(null);
    api.context(eventId)
      .then(setContext)
      .catch((reason: Error) => setDetailError(reason.message));
  }, []);

  const reset = () => {
    const initial = {
      ...DEFAULT_FILTERS,
      startMonth: monthFromDate(meta?.earliest_event_at ?? null),
      endMonth: monthFromDate(meta?.latest_event_at ?? null),
      sourceType: meta?.source_types.length === 1 ? meta.source_types[0] : "",
    };
    setDraftFilters(initial);
    setFilters(initial);
    setSelectedTopicId(null);
    setOccurrenceOffset(0);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div><span className="brand-mark" /> <strong>MNEMOSYNE</strong></div>
        <p>Lokalny atlas pamięci · API {meta?.api_version ?? "—"}</p>
      </header>
      <div className="workspace">
        <aside className="left-panel">
          <div className="panel-heading"><span>01</span><h1>Eksploruj</h1></div>
          <label className="search-box">
            <span>Wyszukaj termin lub temat</span>
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="np. projekt" />
          </label>
          <TopicSearchResults query={search} results={searchResults} onSelect={selectResult} />
          <label className="search-box">
            <span>Wyszukaj treść</span>
            <input value={contentSearch} onChange={(event) => setContentSearch(event.target.value)} placeholder="proza, kod lub log" />
          </label>
          <label>
            Zakres treści
            <select value={contentScope} onChange={(event) => setContentScope(event.target.value as typeof contentScope)}>
              <option value="all">Wszystko</option>
              <option value="prose">Proza</option>
              <option value="code">Kod</option>
              <option value="commands">Komendy</option>
              <option value="logs">Logi</option>
            </select>
          </label>
          <ContentSearchResults query={contentSearch} results={contentResults} onSelect={loadContext} />
          <FilterPanel
            filters={draftFilters}
            meta={meta}
            selectedTopicId={selectedTopicId}
            onChange={setDraftFilters}
            onApply={() => { setFilters(draftFilters); setOccurrenceOffset(0); }}
            onReset={reset}
          />
          <div className="dataset-summary">
            <span>{meta?.counts.events.toLocaleString("pl-PL") ?? "—"}<small>zdarzeń</small></span>
            <span>{meta?.counts.topics.toLocaleString("pl-PL") ?? "—"}<small>tematów</small></span>
            <span>{meta?.counts.relations.toLocaleString("pl-PL") ?? "—"}<small>relacji</small></span>
          </div>
        </aside>

        <section className="graph-panel" aria-label="Graf terminów i tematów">
          <div className="graph-caption">
            <span>{graph?.nodes.length ?? 0} węzłów · {graph?.edges.length ?? 0} relacji</span>
            <span>{filters.graphView === "topics" ? "tematy beta" : "terminy"} · rozmiar = intensywność okresu</span>
          </div>
          <StatusPanel loading={loading} error={error} empty={!loading && !error && graph?.nodes.length === 0} />
          {graph && graph.nodes.length > 0 && <GraphCanvas data={graph} selectedTopicId={selectedTopicId} onSelect={selectGraphNode} />}
        </section>

        <aside className="right-panel">
          <div className="panel-heading"><span>02</span><h1>Szczegóły</h1></div>
          <TopicDetails
            detail={detail}
            occurrences={occurrences}
            context={context}
            loading={detailLoading}
            error={detailError}
            onSelectNeighbor={(nodeId) => selectResult(nodeId, filters.graphView)}
            onLoadContext={loadContext}
            onPage={setOccurrenceOffset}
          />
        </aside>
      </div>

      <footer className="timeline-panel">
        <div><span>03</span><strong>Zakres czasu</strong></div>
        <label>Od <input type="month" value={draftFilters.startMonth} max={draftFilters.endMonth || undefined} onChange={(event) => setDraftFilters({ ...draftFilters, startMonth: event.target.value })} /></label>
        <div className="timeline-track"><span /></div>
        <label>Do <input type="month" value={draftFilters.endMonth} min={draftFilters.startMonth || undefined} onChange={(event) => setDraftFilters({ ...draftFilters, endMonth: event.target.value })} /></label>
        <button className="primary" type="button" onClick={() => { setFilters(draftFilters); setOccurrenceOffset(0); }}>Aktualizuj</button>
      </footer>
    </main>
  );
}

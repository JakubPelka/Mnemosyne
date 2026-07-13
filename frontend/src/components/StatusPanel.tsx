interface StatusPanelProps {
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
}

export function StatusPanel({ loading, error, empty }: StatusPanelProps) {
  if (loading) return <div className="status-card">Ładowanie lokalnego atlasu…</div>;
  if (error)
    return (
      <div className="status-card error" role="alert">
        Backend jest niedostępny. {error}
      </div>
    );
  if (empty)
    return (
      <div className="status-card">
        Brak tematów dla wybranych filtrów. Zmniejsz progi lub zmień zakres czasu.
      </div>
    );
  return null;
}

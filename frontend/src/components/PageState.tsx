export function LoadingState({ label = "Loading data…" }: { label?: string }) {
  return (
    <div className="state-card" role="status">
      <span className="spinner" />
      <h2>{label}</h2>
      <p>Please wait a moment.</p>
    </div>
  );
}
export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry: () => void;
}) {
  return (
    <div className="state-card state-card--error" role="alert">
      <div className="state-icon">!</div>
      <h2>We couldn't load the data</h2>
      <p>{message}</p>
      <button className="button button--secondary" onClick={retry}>
        Try again
      </button>
    </div>
  );
}
export function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="state-card">
      <div className="state-icon state-icon--empty">○</div>
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}

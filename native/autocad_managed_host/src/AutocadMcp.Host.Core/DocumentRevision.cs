namespace AutocadMcp.Host.Core;

public enum DocumentEventKind
{
    ObjectAppended,
    ObjectModified,
    ObjectErased,
    DocumentSaved,
    DocumentActivated,
    DocumentClosing
}

public sealed record DocumentRevisionEvidence(
    long Revision,
    long EventSequence,
    string RevisionStrength,
    DateTimeOffset ObservedAt);

public sealed record DocumentEventBatch(
    long FirstSequence,
    long LastSequence,
    long Revision,
    string Kind,
    int Count,
    IReadOnlyList<string> Handles,
    bool HandlesTruncated,
    DateTimeOffset FirstObservedAt,
    DateTimeOffset LastObservedAt);

public sealed record DocumentEventPage(
    DocumentRevisionEvidence Revision,
    IReadOnlyList<DocumentEventBatch> Events,
    long OldestAvailableSequence,
    bool EventsTruncated);

public sealed class DocumentRevisionState
{
    public const string Strength = "event_and_database";
    private const int MaximumRetainedBatches = 256;
    private const int MaximumHandlesPerBatch = 32;
    private static readonly TimeSpan AggregationWindow = TimeSpan.FromMilliseconds(100);

    private readonly object _gate = new();
    private readonly LinkedList<MutableEventBatch> _batches = new();
    private long _revision = 1;
    private long _eventSequence;
    private int _suppressionDepth;

    public DocumentRevisionEvidence Snapshot(DateTimeOffset observedAt)
    {
        lock (_gate)
        {
            return new(_revision, _eventSequence, Strength, observedAt);
        }
    }

    public void Record(
        DocumentEventKind kind,
        DateTimeOffset observedAt,
        string? handle = null,
        bool changesContent = false)
    {
        lock (_gate)
        {
            if (_suppressionDepth > 0)
            {
                return;
            }
            _eventSequence++;
            if (changesContent)
            {
                _revision++;
            }

            var last = _batches.Last?.Value;
            if (last is not null &&
                last.Kind == kind &&
                observedAt >= last.LastObservedAt &&
                observedAt - last.LastObservedAt <= AggregationWindow)
            {
                last.Add(_eventSequence, _revision, observedAt, handle);
                return;
            }

            _batches.AddLast(new MutableEventBatch(
                _eventSequence,
                _revision,
                kind,
                observedAt,
                handle));
            if (_batches.Count > MaximumRetainedBatches)
            {
                _batches.RemoveFirst();
            }
        }
    }

    public IDisposable SuppressChanges()
    {
        lock (_gate)
        {
            _suppressionDepth++;
        }
        return new Suppression(this);
    }

    public void AssertRevision(long expectedRevision, DateTimeOffset observedAt)
    {
        var actual = Snapshot(observedAt);
        if (actual.Revision != expectedRevision)
        {
            throw new ProtocolValidationException(
                "stale_snapshot",
                $"Document revision changed from {expectedRevision} to {actual.Revision}.");
        }
    }

    public DocumentEventPage ReadEvents(long afterSequence, int maximumEvents, DateTimeOffset observedAt)
    {
        if (afterSequence < 0 || maximumEvents is < 1 or > 100)
        {
            throw new ProtocolValidationException(
                "invalid_envelope",
                "Event cursor or page size is outside the allowed range.");
        }

        lock (_gate)
        {
            var oldest = _batches.First?.Value.FirstSequence ?? (_eventSequence + 1);
            if (_batches.Count > 0 && afterSequence > 0 && afterSequence < oldest - 1)
            {
                throw new ProtocolValidationException(
                    "stale_event_cursor",
                    "The event cursor is older than the retained event window.");
            }

            var candidates = _batches
                .Where(batch => batch.LastSequence > afterSequence)
                .ToArray();
            var events = candidates
                .Take(maximumEvents)
                .Select(batch => batch.Freeze())
                .ToArray();
            return new(
                new(_revision, _eventSequence, Strength, observedAt),
                events,
                oldest,
                candidates.Length > events.Length);
        }
    }

    private sealed class MutableEventBatch
    {
        private readonly List<string> _handles = [];

        public MutableEventBatch(
            long sequence,
            long revision,
            DocumentEventKind kind,
            DateTimeOffset observedAt,
            string? handle)
        {
            FirstSequence = sequence;
            LastSequence = sequence;
            Revision = revision;
            Kind = kind;
            FirstObservedAt = observedAt;
            LastObservedAt = observedAt;
            AddHandle(handle);
        }

        public long FirstSequence { get; }
        public long LastSequence { get; private set; }
        public long Revision { get; private set; }
        public DocumentEventKind Kind { get; }
        public int Count { get; private set; } = 1;
        public bool HandlesTruncated { get; private set; }
        public DateTimeOffset FirstObservedAt { get; }
        public DateTimeOffset LastObservedAt { get; private set; }

        public void Add(long sequence, long revision, DateTimeOffset observedAt, string? handle)
        {
            LastSequence = sequence;
            Revision = revision;
            Count++;
            LastObservedAt = observedAt;
            AddHandle(handle);
        }

        public DocumentEventBatch Freeze() => new(
            FirstSequence,
            LastSequence,
            Revision,
            Kind.ToString().ToLowerInvariant(),
            Count,
            _handles.ToArray(),
            HandlesTruncated,
            FirstObservedAt,
            LastObservedAt);

        private void AddHandle(string? handle)
        {
            if (string.IsNullOrEmpty(handle) || _handles.Contains(handle, StringComparer.Ordinal))
            {
                return;
            }
            if (_handles.Count >= MaximumHandlesPerBatch)
            {
                HandlesTruncated = true;
                return;
            }
            _handles.Add(handle);
        }
    }

    private sealed class Suppression(DocumentRevisionState owner) : IDisposable
    {
        private DocumentRevisionState? _owner = owner;

        public void Dispose()
        {
            var current = Interlocked.Exchange(ref _owner, null);
            if (current is null)
            {
                return;
            }
            lock (current._gate)
            {
                current._suppressionDepth--;
            }
        }
    }
}

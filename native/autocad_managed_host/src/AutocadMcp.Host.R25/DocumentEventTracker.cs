using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;

namespace AutocadMcp.Host.R25;

internal sealed class DocumentEventTracker : IDisposable
{
    private readonly DocumentCollection _documents;
    private readonly DocumentIdentityRegistry _identities;
    private readonly Dictionary<Document, Subscription> _subscriptions = [];
    private bool _disposed;

    public DocumentEventTracker(
        DocumentCollection documents,
        DocumentIdentityRegistry identities)
    {
        _documents = documents;
        _identities = identities;
        _documents.DocumentCreated += OnDocumentCreated;
        _documents.DocumentActivated += OnDocumentActivated;
        _documents.DocumentToBeDestroyed += OnDocumentToBeDestroyed;
        foreach (Document document in _documents)
        {
            Attach(document);
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _documents.DocumentCreated -= OnDocumentCreated;
        _documents.DocumentActivated -= OnDocumentActivated;
        _documents.DocumentToBeDestroyed -= OnDocumentToBeDestroyed;
        foreach (var document in _subscriptions.Keys.ToArray())
        {
            Detach(document);
        }
    }

    private void OnDocumentCreated(object sender, DocumentCollectionEventArgs args) =>
        Attach(args.Document);

    private void OnDocumentActivated(object sender, DocumentCollectionEventArgs args)
    {
        Attach(args.Document);
        Record(args.Document, DocumentEventKind.DocumentActivated);
    }

    private void OnDocumentToBeDestroyed(object sender, DocumentCollectionEventArgs args)
    {
        Record(args.Document, DocumentEventKind.DocumentClosing);
        Detach(args.Document);
    }

    private void Attach(Document document)
    {
        if (_disposed || _subscriptions.ContainsKey(document))
        {
            return;
        }

        ObjectEventHandler appended = (_, args) =>
            RecordObject(document, DocumentEventKind.ObjectAppended, args.DBObject);
        ObjectEventHandler modified = (_, args) =>
            RecordObject(document, DocumentEventKind.ObjectModified, args.DBObject);
        ObjectErasedEventHandler erased = (_, args) =>
        {
            if (args.Erased)
            {
                RecordObject(document, DocumentEventKind.ObjectErased, args.DBObject);
            }
        };
        DatabaseIOEventHandler saved = (_, _) =>
            Record(document, DocumentEventKind.DocumentSaved);

        document.Database.ObjectAppended += appended;
        document.Database.ObjectModified += modified;
        document.Database.ObjectErased += erased;
        document.Database.SaveComplete += saved;
        _subscriptions.Add(document, new(appended, modified, erased, saved));
        _ = _identities.Get(document);
    }

    private void Detach(Document document)
    {
        if (!_subscriptions.Remove(document, out var subscription))
        {
            return;
        }
        document.Database.ObjectAppended -= subscription.Appended;
        document.Database.ObjectModified -= subscription.Modified;
        document.Database.ObjectErased -= subscription.Erased;
        document.Database.SaveComplete -= subscription.Saved;
    }

    private void RecordObject(Document document, DocumentEventKind kind, DBObject value)
    {
        string? handle = null;
        try
        {
            handle = value.Handle.ToString();
        }
        catch
        {
        }
        _identities.Get(document).Revision.Record(
            kind,
            DateTimeOffset.UtcNow,
            handle,
            changesContent: true);
    }

    private void Record(Document document, DocumentEventKind kind) =>
        _identities.Get(document).Revision.Record(kind, DateTimeOffset.UtcNow);

    private sealed record Subscription(
        ObjectEventHandler Appended,
        ObjectEventHandler Modified,
        ObjectErasedEventHandler Erased,
        DatabaseIOEventHandler Saved);
}

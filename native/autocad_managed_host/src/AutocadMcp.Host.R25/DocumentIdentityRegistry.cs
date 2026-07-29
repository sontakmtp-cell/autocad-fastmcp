using System.Runtime.CompilerServices;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices;

namespace AutocadMcp.Host.R25;

internal sealed class DocumentIdentityRegistry
{
    private readonly ConditionalWeakTable<Document, Entry> _entries = new();
    private readonly DocumentRevisionCheckpointStore _checkpoints = new(
        Path.Combine(
            Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData),
            "KythuatVang",
            "AutoCADMcp",
            "document-revisions"));

    public DocumentIdentity Get(Document document)
    {
        var entry = _entries.GetValue(document, CreateEntry);
        return new(
            entry.DocumentId,
            GetDatabaseFingerprint(document),
            entry.Revision);
    }

    private Entry CreateEntry(Document document)
    {
        var fingerprint = GetDatabaseFingerprint(document);
        var documentId = fingerprint == "unavailable"
            ? $"doc-session-{Environment.ProcessId}-{RuntimeHelpers.GetHashCode(document):x}"
            : StableDocumentIdentity.FromDatabaseFingerprint(fingerprint);
        var initialRevision =
            fingerprint != "unavailable" &&
            _checkpoints.TryRead(
                documentId,
                fingerprint,
                document.Name,
                out var persistedRevision)
                ? persistedRevision
                : DocumentRevisionState.CreateIncarnationSeed();
        return new(
            documentId,
            new DocumentRevisionState(initialRevision));
    }

    public void Persist(Document document)
    {
        try
        {
            var identity = Get(document);
            _checkpoints.Write(
                identity.DocumentId,
                identity.DatabaseFingerprint,
                document.Name,
                identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision);
        }
        catch
        {
            // Revision recovery is optional evidence. A sidecar I/O failure
            // must never escape AutoCAD's SaveComplete event.
        }
    }

    private static string GetDatabaseFingerprint(Document document)
    {
        try
        {
            return document.Database.FingerprintGuid.ToString();
        }
        catch
        {
            return "unavailable";
        }
    }

    private sealed record Entry(string DocumentId, DocumentRevisionState Revision);
}

internal sealed record DocumentIdentity(
    string DocumentId,
    string DatabaseFingerprint,
    DocumentRevisionState Revision);

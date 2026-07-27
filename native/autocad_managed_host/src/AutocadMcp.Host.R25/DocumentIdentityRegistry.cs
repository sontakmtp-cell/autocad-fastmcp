using System.Runtime.CompilerServices;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices;

namespace AutocadMcp.Host.R25;

internal sealed class DocumentIdentityRegistry
{
    private readonly ConditionalWeakTable<Document, Entry> _entries = new();

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
        return new(
            documentId,
            new DocumentRevisionState(DocumentRevisionState.CreateIncarnationSeed()));
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

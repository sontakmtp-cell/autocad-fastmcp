using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices;

namespace AutocadMcp.Host.R25;

internal sealed class DocumentIdentityRegistry(byte[] identityKey)
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
        using var hmac = new HMACSHA256(identityKey);
        var nonce = RandomNumberGenerator.GetBytes(16);
        var input = Encoding.UTF8.GetBytes(
            $"{Environment.ProcessId}\n{RuntimeHelpers.GetHashCode(document)}\n{Convert.ToHexString(nonce)}");
        var digest = Convert.ToHexString(hmac.ComputeHash(input)).ToLowerInvariant();
        return new($"doc-{digest[..24]}", new DocumentRevisionState());
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

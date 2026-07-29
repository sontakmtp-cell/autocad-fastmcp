using System.Security.Cryptography;
using System.Text.Json;

namespace AutocadMcp.Host.Core;

public sealed class DocumentRevisionCheckpointStore(string rootDirectory)
{
    private const string Schema = "cad.document-revision-checkpoint/1";
    private const int MaximumRecordBytes = 4096;

    public bool TryRead(
        string documentId,
        string databaseFingerprint,
        string drawingPath,
        out long revision)
    {
        revision = 0;
        try
        {
            var path = RecordPath(documentId);
            if (!File.Exists(path) || !File.Exists(drawingPath))
            {
                return false;
            }
            var bytes = File.ReadAllBytes(path);
            if (bytes.Length is 0 or > MaximumRecordBytes)
            {
                return false;
            }
            var value = JsonSerializer.Deserialize<RevisionRecord>(bytes);
            if (value is null ||
                value.SchemaVersion != Schema ||
                value.DocumentId != documentId ||
                value.DatabaseFingerprint != databaseFingerprint ||
                value.DrawingSha256 != FileDigest(drawingPath) ||
                value.Revision < 1)
            {
                return false;
            }
            revision = value.Revision;
            return true;
        }
        catch
        {
            return false;
        }
    }

    public void Write(
        string documentId,
        string databaseFingerprint,
        string drawingPath,
        long revision)
    {
        if (revision < 1 || !File.Exists(drawingPath))
        {
            return;
        }
        Directory.CreateDirectory(rootDirectory);
        var path = RecordPath(documentId);
        var temporary = $"{path}.{Guid.NewGuid():N}.tmp";
        try
        {
            var bytes = JsonSerializer.SerializeToUtf8Bytes(
                new RevisionRecord(
                    Schema,
                    documentId,
                    databaseFingerprint,
                    FileDigest(drawingPath),
                    revision));
            if (bytes.Length > MaximumRecordBytes)
            {
                throw new InvalidOperationException(
                    "Document revision checkpoint exceeds its bounded record size.");
            }
            File.WriteAllBytes(temporary, bytes);
            File.Move(temporary, path, true);
        }
        finally
        {
            if (File.Exists(temporary))
            {
                File.Delete(temporary);
            }
        }
    }

    private string RecordPath(string documentId)
    {
        var digest = Convert.ToHexString(
            SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(documentId)))
            .ToLowerInvariant();
        return Path.Combine(rootDirectory, $"{digest}.json");
    }

    private static string FileDigest(string path)
    {
        using var stream = File.Open(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private sealed record RevisionRecord(
        string SchemaVersion,
        string DocumentId,
        string DatabaseFingerprint,
        string DrawingSha256,
        long Revision);
}

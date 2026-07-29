using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class DocumentRevisionCheckpointStoreTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        $"autocad-mcp-revision-{Guid.NewGuid():N}");

    [Fact]
    public void RestoresOnlyTheExactSavedDrawingRevision()
    {
        Directory.CreateDirectory(_root);
        var drawing = Path.Combine(_root, "drawing33.dwg");
        File.WriteAllText(drawing, "saved-drawing-a");
        var store = new DocumentRevisionCheckpointStore(
            Path.Combine(_root, "checkpoints"));

        store.Write("document-a", "fingerprint-a", drawing, 42);

        Assert.True(store.TryRead(
            "document-a", "fingerprint-a", drawing, out var revision));
        Assert.Equal(42, revision);

        File.WriteAllText(drawing, "saved-drawing-b");
        Assert.False(store.TryRead(
            "document-a", "fingerprint-a", drawing, out _));
    }

    [Fact]
    public void RejectsAnotherDocumentOrDatabaseFingerprint()
    {
        Directory.CreateDirectory(_root);
        var drawing = Path.Combine(_root, "drawing33.dwg");
        File.WriteAllText(drawing, "saved-drawing");
        var store = new DocumentRevisionCheckpointStore(
            Path.Combine(_root, "checkpoints"));
        store.Write("document-a", "fingerprint-a", drawing, 9);

        Assert.False(store.TryRead(
            "document-b", "fingerprint-a", drawing, out _));
        Assert.False(store.TryRead(
            "document-a", "fingerprint-b", drawing, out _));
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, true);
        }
    }
}

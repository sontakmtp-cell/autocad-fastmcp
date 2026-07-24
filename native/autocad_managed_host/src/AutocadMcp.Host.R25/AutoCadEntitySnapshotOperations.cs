using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadEntitySnapshotOperations(DocumentIdentityRegistry identities)
{
    public object ReadPage(CommandRequest command)
    {
        var request = EntitySnapshotRequest.Parse(command.Arguments);
        var document = RequireAvailableDocument();
        var identity = identities.Get(document);
        AssertDocument(command, document, identity);
        if (request.ExpectedRevision is { } expectedRevision)
        {
            identity.Revision.AssertRevision(expectedRevision, DateTimeOffset.UtcNow);
        }
        var before = identity.Revision.Snapshot(DateTimeOffset.UtcNow);

        using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
        var blockTable = (BlockTable)transaction.GetObject(
            document.Database.BlockTableId,
            OpenMode.ForRead);
        var spaces = blockTable
            .Cast<ObjectId>()
            .Select(id => (BlockTableRecord)transaction.GetObject(id, OpenMode.ForRead))
            .Where(record => record.IsLayout && IncludesSpace(record, request.Space))
            .OrderBy(record => record.Name, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var entities = new List<object>(request.Limit);
        var absoluteIndex = 0;
        var scanned = 0;
        var exhausted = true;
        foreach (var space in spaces)
        {
            foreach (var objectId in space.Cast<ObjectId>())
            {
                if (absoluteIndex++ < request.Cursor)
                {
                    continue;
                }
                if (scanned >= request.MaxScan || entities.Count >= request.Limit)
                {
                    exhausted = false;
                    break;
                }
                scanned++;
                if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity ||
                    !Matches(entity, request))
                {
                    continue;
                }
                entities.Add(ToMetadata(entity, SpaceName(space)));
            }
            if (!exhausted)
            {
                break;
            }
        }

        AssertStillCurrent(document, identity, before.Revision);
        var nextCursor = exhausted ? (int?)null : request.Cursor + scanned;
        return new
        {
            document_id = identity.DocumentId,
            document_name = Bound(Path.GetFileName(document.Name), 255),
            database_fingerprint = identity.DatabaseFingerprint,
            revision = before,
            cursor = request.Cursor,
            next_cursor = nextCursor,
            limit = request.Limit,
            scanned_count = scanned,
            returned_count = entities.Count,
            scan_truncated = !exhausted && scanned >= request.MaxScan,
            entities
        };
    }

    public object ReadEvents(CommandRequest command)
    {
        var request = DocumentEventsRequest.Parse(command.Arguments);
        var document = RequireAvailableDocument();
        var identity = identities.Get(document);
        AssertDocument(command, document, identity);
        if (request.ExpectedRevision is { } expectedRevision)
        {
            identity.Revision.AssertRevision(expectedRevision, DateTimeOffset.UtcNow);
        }
        var events = identity.Revision.ReadEvents(
            request.AfterSequence,
            request.MaxEvents,
            DateTimeOffset.UtcNow);
        AssertStillCurrent(document, identity, events.Revision.Revision);
        return new
        {
            document_id = identity.DocumentId,
            document_name = Bound(Path.GetFileName(document.Name), 255),
            database_fingerprint = identity.DatabaseFingerprint,
            revision = events.Revision,
            events = events.Events,
            oldest_available_sequence = events.OldestAvailableSequence,
            events_truncated = events.EventsTruncated
        };
    }

    private static Document RequireAvailableDocument()
    {
        var document = Application.DocumentManager.MdiActiveDocument
            ?? throw new ProtocolValidationException(
                "no_active_document",
                "No active drawing is open.");
        var commandActive = GetCommandActive();
        if ((commandActive & 8) != 0)
        {
            throw new ProtocolValidationException(
                "modal_dialog_active",
                "AutoCAD is waiting for a modal dialog.");
        }
        if (commandActive != 0)
        {
            throw new ProtocolValidationException(
                "autocad_busy",
                "AutoCAD is executing another command.");
        }
        return document;
    }

    private static void AssertDocument(
        CommandRequest command,
        Document document,
        DocumentIdentity identity)
    {
        if (command.DocumentId is not null && command.DocumentId != identity.DocumentId)
        {
            throw new ProtocolValidationException(
                "active_document_changed",
                "The active document changed.");
        }
        if (!ReferenceEquals(Application.DocumentManager.MdiActiveDocument, document))
        {
            throw new ProtocolValidationException(
                "active_document_changed",
                "The active document changed.");
        }
    }

    private static void AssertStillCurrent(
        Document document,
        DocumentIdentity identity,
        long expectedRevision)
    {
        if (!ReferenceEquals(Application.DocumentManager.MdiActiveDocument, document))
        {
            throw new ProtocolValidationException(
                "active_document_changed",
                "The active document changed while reading.");
        }
        identity.Revision.AssertRevision(expectedRevision, DateTimeOffset.UtcNow);
    }

    private static bool IncludesSpace(BlockTableRecord record, string requested) =>
        requested == "all" ||
        requested == "model" && record.Name == BlockTableRecord.ModelSpace ||
        requested == "paper" && record.Name != BlockTableRecord.ModelSpace;

    private static string SpaceName(BlockTableRecord record) =>
        record.Name == BlockTableRecord.ModelSpace ? "model" : "paper";

    private static bool Matches(Entity entity, EntitySnapshotRequest request)
    {
        var type = GetEntityType(entity);
        return (request.Types.Count == 0 || request.Types.Contains(type)) &&
            (request.Layers.Count == 0 || request.Layers.Contains(entity.Layer));
    }

    private static object ToMetadata(Entity entity, string space) => new
    {
        handle = entity.Handle.ToString(),
        type = Bound(GetEntityType(entity), 64),
        layer = Bound(entity.Layer, 255),
        space,
        bounds = TryGetBounds(entity)
    };

    private static string GetEntityType(Entity entity) =>
        entity.GetRXClass().DxfName ?? entity.GetType().Name;

    private static object? TryGetBounds(Entity entity)
    {
        try
        {
            var extents = entity.GeometricExtents;
            return IsFinite(extents.MinPoint) && IsFinite(extents.MaxPoint)
                ? new
                {
                    min = new[] { extents.MinPoint.X, extents.MinPoint.Y, extents.MinPoint.Z },
                    max = new[] { extents.MaxPoint.X, extents.MaxPoint.Y, extents.MaxPoint.Z }
                }
                : null;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception)
        {
            return null;
        }
    }

    private static bool IsFinite(Point3d point) =>
        double.IsFinite(point.X) && double.IsFinite(point.Y) && double.IsFinite(point.Z);

    private static int GetCommandActive()
    {
        try
        {
            return Convert.ToInt32(Application.GetSystemVariable("CMDACTIVE"));
        }
        catch
        {
            return 1;
        }
    }

    private static string Bound(string value, int maximum) =>
        value.Length <= maximum ? value : value[..maximum];
}

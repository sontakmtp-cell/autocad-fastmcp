using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadEntitySnapshotOperations(DocumentIdentityRegistry identities)
{
    private static readonly string[] Phase10GeometryCapabilities =
    [
        "entity.geometry.arc/1",
        "entity.geometry.circle/1",
        "entity.geometry.line/1",
        "entity.geometry.polyline/1"
    ];

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
        var spaces = new List<BlockTableRecord>();
        if (request.Space is "all" or "model")
        {
            spaces.Add((BlockTableRecord)transaction.GetObject(
                blockTable[BlockTableRecord.ModelSpace],
                OpenMode.ForRead));
        }
        if (request.Space is "all" or "paper")
        {
            spaces.AddRange(blockTable
                .Cast<ObjectId>()
                .Select(id => (BlockTableRecord)transaction.GetObject(
                    id,
                    OpenMode.ForRead))
                .Where(record =>
                    record.IsLayout &&
                    record.Name != BlockTableRecord.ModelSpace)
                .OrderBy(record => record.Name, StringComparer.OrdinalIgnoreCase));
        }

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
                entities.Add(ToMetadata(
                    entity,
                    SpaceName(space),
                    transaction));
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
            source_capabilities = Phase10GeometryCapabilities,
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

    private static string SpaceName(BlockTableRecord record) =>
        string.Equals(
            record.Name,
            BlockTableRecord.ModelSpace,
            StringComparison.OrdinalIgnoreCase)
            ? "model"
            : "paper";

    private static bool Matches(Entity entity, EntitySnapshotRequest request)
    {
        var type = GetEntityType(entity);
        return (request.Types.Count == 0 || request.Types.Contains(type)) &&
            (request.Layers.Count == 0 || request.Layers.Contains(entity.Layer));
    }

    private static object ToMetadata(
        Entity entity,
        string space,
        Transaction transaction)
    {
        var projection = ProjectGeometry(entity);
        return new
        {
            handle = entity.Handle.ToString(),
            type = Bound(GetEntityType(entity), 64),
            layer = Bound(entity.Layer, 255),
            space,
            bounds = TryGetBounds(entity),
            geometry = projection.Geometry,
            geometry_status = projection.Status,
            geometry_reason = projection.Reason,
            source_capabilities = projection.Capabilities,
            geometry_truncated = projection.Status == "truncated",
            fingerprint = Phase8ManagedOperationPack.EntityFingerprint(
                entity,
                transaction)
        };
    }

    private static GeometryProjection ProjectGeometry(Entity entity)
    {
        try
        {
            return entity switch
            {
                Line line => ProjectLine(line),
                Circle circle => ProjectCircle(circle),
                Polyline polyline => ProjectPolyline(polyline),
                Arc arc => ProjectArc(arc),
                _ => Unsupported("entity_type_unsupported")
            };
        }
        catch (Autodesk.AutoCAD.Runtime.Exception)
        {
            return new(null, "unavailable", "autodesk_read_failed", []);
        }
    }

    private static GeometryProjection ProjectLine(Line line)
    {
        if (!IsFinite(line.StartPoint) || !IsFinite(line.EndPoint))
        {
            return Invalid();
        }
        return Exact(
            new
            {
                start = new[] { line.StartPoint.X, line.StartPoint.Y },
                end = new[] { line.EndPoint.X, line.EndPoint.Y },
                start_elevation = line.StartPoint.Z,
                end_elevation = line.EndPoint.Z
            },
            "entity.geometry.line/1");
    }

    private static GeometryProjection ProjectCircle(Circle circle)
    {
        if (!IsFinite(circle.Center) ||
            !double.IsFinite(circle.Radius) ||
            !IsFinite(circle.Normal))
        {
            return Invalid();
        }
        return Exact(
            new
            {
                center = new[] { circle.Center.X, circle.Center.Y },
                radius = circle.Radius,
                elevation = circle.Center.Z,
                normal = new[] { circle.Normal.X, circle.Normal.Y, circle.Normal.Z }
            },
            "entity.geometry.circle/1");
    }

    private static GeometryProjection ProjectPolyline(Polyline polyline)
    {
        if (polyline.NumberOfVertices > 4096)
        {
            return new(null, "truncated", "vertex_limit_exceeded",
                ["entity.geometry.polyline/1"]);
        }
        var points = Enumerable.Range(0, polyline.NumberOfVertices)
            .Select(polyline.GetPoint2dAt)
            .ToArray();
        var bulges = Enumerable.Range(0, polyline.NumberOfVertices)
            .Select(polyline.GetBulgeAt)
            .ToArray();
        if (points.Any(point => !double.IsFinite(point.X) || !double.IsFinite(point.Y)) ||
            bulges.Any(value => !double.IsFinite(value)) ||
            !double.IsFinite(polyline.Elevation) ||
            !IsFinite(polyline.Normal))
        {
            return Invalid();
        }
        return Exact(
            new
            {
                points = points.Select(point => new[] { point.X, point.Y }).ToArray(),
                bulges,
                closed = polyline.Closed,
                elevation = polyline.Elevation,
                normal = new[] { polyline.Normal.X, polyline.Normal.Y, polyline.Normal.Z }
            },
            "entity.geometry.polyline/1");
    }

    private static GeometryProjection ProjectArc(Arc arc)
    {
        if (!IsFinite(arc.Center) ||
            !double.IsFinite(arc.Radius) ||
            !double.IsFinite(arc.StartAngle) ||
            !double.IsFinite(arc.EndAngle) ||
            !IsFinite(arc.Normal))
        {
            return Invalid();
        }
        return Exact(
            new
            {
                center = new[] { arc.Center.X, arc.Center.Y },
                radius = arc.Radius,
                start_angle = arc.StartAngle,
                end_angle = arc.EndAngle,
                elevation = arc.Center.Z,
                normal = new[] { arc.Normal.X, arc.Normal.Y, arc.Normal.Z }
            },
            "entity.geometry.arc/1");
    }

    private static GeometryProjection Exact(object geometry, string capability) =>
        new(geometry, "exact", null, [capability]);

    private static GeometryProjection Unsupported(string reason) =>
        new(null, "unsupported", reason, []);

    private static GeometryProjection Invalid() =>
        new(null, "invalid", "non_finite_geometry", []);

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

    private static bool IsFinite(Vector3d vector) =>
        double.IsFinite(vector.X) && double.IsFinite(vector.Y) && double.IsFinite(vector.Z);

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

    private sealed record GeometryProjection(
        object? Geometry,
        string Status,
        string? Reason,
        string[] Capabilities);
}

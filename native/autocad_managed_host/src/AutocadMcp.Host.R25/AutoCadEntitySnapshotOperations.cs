using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
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
        "entity.geometry.polyline/1",
        "entity.properties.dimension/1"
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
        var detailErrorCount = 0;
        var exhausted = true;
        foreach (var space in spaces)
        {
            foreach (var objectId in space.Cast<ObjectId>())
            {
                var entityIndex = absoluteIndex++;
                if (entityIndex < request.Cursor)
                {
                    continue;
                }
                if (scanned >= request.MaxScan || entities.Count >= request.Limit)
                {
                    exhausted = false;
                    break;
                }
                scanned++;

                Entity? entity = null;
                try
                {
                    if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity openedEntity)
                    {
                        continue;
                    }
                    entity = openedEntity;
                    if (!Matches(entity, request))
                    {
                        continue;
                    }
                    var metadata = ToMetadata(
                        entity,
                        SpaceName(space),
                        transaction,
                        entityIndex);
                    detailErrorCount += metadata.DetailErrorCount;
                    entities.Add(metadata.Value);
                }
                catch (Exception error) when (!IsFatal(error))
                {
                    detailErrorCount++;
                    entities.Add(ToEntityErrorMetadata(
                        objectId,
                        entity,
                        SpaceName(space),
                        entityIndex,
                        error));
                }
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
            detail_error_count = detailErrorCount,
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

    private static EntityMetadataResult ToMetadata(
        Entity entity,
        string space,
        Transaction transaction,
        int index)
    {
        var errors = new List<object>();
        var handle = ReadValue(
            index,
            "Handle",
            () => entity.Handle.ToString(),
            $"UNAVAILABLE-{index}",
            errors);
        var objectName = ReadValue(
            index,
            "ObjectName",
            () => entity.GetRXClass().Name ?? entity.GetType().Name,
            entity.GetType().Name,
            errors,
            handle);
        var type = ReadValue(
            index,
            "DXFType",
            () => Bound(GetEntityType(entity), 64),
            "UNKNOWN",
            errors,
            handle,
            objectName);
        var layer = ReadValue(
            index,
            "Layer",
            () => Bound(entity.Layer, 255),
            "<unavailable>",
            errors,
            handle,
            objectName,
            type);
        var projection = ReadValue(
            index,
            "Geometry",
            () => ProjectGeometry(
                entity,
                transaction,
                index,
                handle,
                objectName,
                type,
                layer,
                errors),
            new GeometryProjection(null, "unavailable", "entity_read_failed", []),
            errors,
            handle,
            objectName,
            type);
        var bounds = ReadValue<object?>(
            index,
            "GeometricExtents",
            () => TryGetBounds(entity),
            null,
            errors,
            handle,
            objectName,
            type);
        var fingerprint = ReadValue(
            index,
            "Fingerprint",
            () => ReadFingerprint(entity, transaction, bounds, projection, type, layer),
            ErrorFingerprint(index, handle, type, layer, "fingerprint_unavailable"),
            errors,
            handle,
            objectName,
            type);

        var value = new
        {
            handle,
            type,
            object_name = objectName,
            layer,
            space,
            bounds,
            geometry = projection.Geometry,
            geometry_status = projection.Status,
            geometry_reason = projection.Reason,
            source_capabilities = projection.Capabilities,
            geometry_truncated = projection.Status == "truncated",
            detail_errors = errors,
            fingerprint
        };
        return new EntityMetadataResult(value, errors.Count);
    }

    private static object ToEntityErrorMetadata(
        ObjectId objectId,
        Entity? entity,
        string space,
        int index,
        Exception error)
    {
        var handle = SafeHandle(entity, objectId, index);
        var objectName = SafeObjectName(entity);
        var type = SafeEntityType(entity);
        var layer = SafeLayer(entity);
        LogDetailError(index, handle, objectName, type, "entity", error);
        var detailError = new
        {
            property = "entity",
            error_type = Bound(error.GetType().Name, 128),
            message = Bound(error.Message ?? "entity read failed", 512)
        };
        return new
        {
            handle,
            type,
            object_name = objectName,
            layer,
            space,
            bounds = (object?)null,
            geometry = new { detail_error = detailError },
            geometry_status = "unavailable",
            geometry_reason = "entity_read_failed",
            source_capabilities = Array.Empty<string>(),
            geometry_truncated = false,
            detail_errors = new[] { detailError },
            fingerprint = ErrorFingerprint(index, handle, type, layer, error.GetType().Name)
        };
    }

    private static string ReadFingerprint(
        Entity entity,
        Transaction transaction,
        object? bounds,
        GeometryProjection projection,
        string type,
        string layer)
    {
        if (entity is Line or Circle or Polyline)
        {
            try
            {
                return Phase8ManagedOperationPack.EntityFingerprint(entity, transaction);
            }
            catch (Exception error) when (!IsFatal(error))
            {
                System.Diagnostics.Trace.WriteLine($"DETAIL_OBSERVE_FINGERPRINT_FALLBACK handle={SafeHandle(entity, default, -1)} error={error}");
            }
        }
        var value = JsonSerializer.SerializeToElement(
            new
            {
                entity_type = type,
                layer,
                bounds,
                geometry = projection.Geometry,
                geometry_status = projection.Status,
                geometry_reason = projection.Reason
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(value)}";
    }

    private static GeometryProjection ProjectGeometry(
        Entity entity,
        Transaction transaction,
        int index,
        string handle,
        string objectName,
        string type,
        string layer,
        List<object> errors)
    {
        return entity switch
        {
            Line line => ProjectLine(line),
            Circle circle => ProjectCircle(circle),
            Polyline polyline => ProjectPolyline(polyline),
            Arc arc => ProjectArc(arc),
            Dimension dimension => ProjectDimension(
                dimension,
                transaction,
                index,
                handle,
                objectName,
                type,
                layer,
                errors),
            _ => Unsupported("entity_type_unsupported")
        };
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
                start_angle_radians = arc.StartAngle,
                end_angle_radians = arc.EndAngle,
                elevation = arc.Center.Z,
                normal = new[] { arc.Normal.X, arc.Normal.Y, arc.Normal.Z }
            },
            "entity.geometry.arc/1");
    }

    private static GeometryProjection ProjectDimension(
        Dimension dimension,
        Transaction transaction,
        int index,
        string handle,
        string objectName,
        string type,
        string layer,
        List<object> errors)
    {
        var properties = new Dictionary<string, object?>
        {
            ["ObjectName"] = objectName,
            ["Handle"] = handle,
            ["Layer"] = layer,
            ["DimensionStyle"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "DimensionStyleName", "DimensionStyle"),
            ["Measurement"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "Measurement"),
            ["TextOverride"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "DimensionText", "TextOverride"),
            ["TextHeight"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "TextHeight", "Dimtxt"),
            ["TextPosition"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "TextPosition"),
            ["TextRotation"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "TextRotation"),
            ["Color"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "ColorIndex"),
            ["Visible"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "Visible", "Visibility"),
            ["Annotative"] = ReadFirstProperty(
                dimension, transaction, index, handle, objectName, type, errors,
                "Annotative")
        };
        if (errors.Count > 0)
        {
            properties["detail_errors"] = errors;
        }
        return Exact(properties, "entity.properties.dimension/1");
    }

    private static object? ReadFirstProperty(
        object target,
        Transaction transaction,
        int index,
        string handle,
        string objectName,
        string type,
        List<object> errors,
        params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = target.GetType().GetProperty(
                propertyName,
                BindingFlags.Instance | BindingFlags.Public);
            if (property is null || property.GetIndexParameters().Length != 0)
            {
                continue;
            }
            try
            {
                return NormalizePropertyValue(property.GetValue(target), transaction);
            }
            catch (Exception error) when (!IsFatal(error))
            {
                AddDetailError(
                    errors,
                    index,
                    handle,
                    objectName,
                    type,
                    propertyName,
                    error);
                return null;
            }
        }
        return null;
    }

    private static object? NormalizePropertyValue(object? value, Transaction transaction)
    {
        return value switch
        {
            null => null,
            string text => Bound(text, 4096),
            bool boolean => boolean,
            byte number => number,
            sbyte number => number,
            short number => number,
            ushort number => number,
            int number => number,
            uint number => number,
            long number => number,
            ulong number => number,
            float number when float.IsFinite(number) => number,
            double number when double.IsFinite(number) => number,
            decimal number => number,
            Point2d point when double.IsFinite(point.X) && double.IsFinite(point.Y) =>
                new[] { point.X, point.Y },
            Point3d point when IsFinite(point) =>
                new[] { point.X, point.Y, point.Z },
            Vector3d vector when IsFinite(vector) =>
                new[] { vector.X, vector.Y, vector.Z },
            ObjectId objectId => NormalizeObjectId(objectId, transaction),
            Enum enumeration => enumeration.ToString(),
            _ => Bound(Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture) ?? string.Empty, 4096)
        };
    }

    private static object? NormalizeObjectId(ObjectId objectId, Transaction transaction)
    {
        if (objectId.IsNull)
        {
            return null;
        }
        try
        {
            if (transaction.GetObject(objectId, OpenMode.ForRead, false) is SymbolTableRecord symbol)
            {
                return Bound(symbol.Name, 255);
            }
        }
        catch (Exception error) when (!IsFatal(error))
        {
            System.Diagnostics.Trace.WriteLine($"DETAIL_OBSERVE_OBJECT_ID_FALLBACK object_id={objectId} error={error}");
        }
        try
        {
            return objectId.Handle.ToString();
        }
        catch (Exception error) when (!IsFatal(error))
        {
            return Bound(objectId.ToString(), 255);
        }
    }

    private static T ReadValue<T>(
        int index,
        string property,
        Func<T> read,
        T fallback,
        List<object> errors,
        string handle = "<unavailable>",
        string objectName = "<unavailable>",
        string type = "UNKNOWN")
    {
        try
        {
            return read();
        }
        catch (Exception error) when (!IsFatal(error))
        {
            AddDetailError(errors, index, handle, objectName, type, property, error);
            return fallback;
        }
    }

    private static void AddDetailError(
        List<object> errors,
        int index,
        string handle,
        string objectName,
        string type,
        string property,
        Exception error)
    {
        LogDetailError(index, handle, objectName, type, property, error);
        errors.Add(new
        {
            property,
            error_type = Bound(error.GetType().Name, 128),
            message = Bound(error.Message ?? "property read failed", 512)
        });
    }

    private static void LogDetailError(
        int index,
        string handle,
        string objectName,
        string type,
        string property,
        Exception error)
    {
        System.Diagnostics.Trace.WriteLine(
            $"DETAIL_OBSERVE_ERROR index={index} handle={Bound(handle, 128)} " +
            $"object_name={Bound(objectName, 128)} dxf_type={Bound(type, 64)} " +
            $"property={Bound(property, 128)} error={error}");
    }

    private static string SafeHandle(Entity? entity, ObjectId objectId, int index)
    {
        try
        {
            if (entity is not null)
            {
                return entity.Handle.ToString();
            }
            if (!objectId.IsNull)
            {
                return objectId.Handle.ToString();
            }
        }
        catch (Exception error) when (!IsFatal(error))
        {
            System.Diagnostics.Trace.WriteLine($"DETAIL_OBSERVE_HANDLE_FALLBACK index={index} error={error}");
        }
        return $"UNAVAILABLE-{index}";
    }

    private static string SafeObjectName(Entity? entity)
    {
        if (entity is null)
        {
            return "<unavailable>";
        }
        try
        {
            return Bound(entity.GetRXClass().Name ?? entity.GetType().Name, 128);
        }
        catch (Exception error) when (!IsFatal(error))
        {
            return Bound(entity.GetType().Name, 128);
        }
    }

    private static string SafeEntityType(Entity? entity)
    {
        if (entity is null)
        {
            return "UNKNOWN";
        }
        try
        {
            return Bound(GetEntityType(entity), 64);
        }
        catch (Exception error) when (!IsFatal(error))
        {
            return "UNKNOWN";
        }
    }

    private static string SafeLayer(Entity? entity)
    {
        if (entity is null)
        {
            return "<unavailable>";
        }
        try
        {
            return Bound(entity.Layer, 255);
        }
        catch (Exception error) when (!IsFatal(error))
        {
            return "<unavailable>";
        }
    }

    private static string ErrorFingerprint(
        int index,
        string handle,
        string type,
        string layer,
        string reason)
    {
        var value = JsonSerializer.SerializeToElement(
            new { index, handle, type, layer, reason },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(value)}";
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
        catch (Exception error) when (!IsFatal(error))
        {
            return null;
        }
    }

    private static bool IsFinite(Point3d point) =>
        double.IsFinite(point.X) && double.IsFinite(point.Y) && double.IsFinite(point.Z);

    private static bool IsFinite(Vector3d vector) =>
        double.IsFinite(vector.X) && double.IsFinite(vector.Y) && double.IsFinite(vector.Z);

    private static bool IsFatal(Exception error) => error is OutOfMemoryException;

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

    private sealed record EntityMetadataResult(object Value, int DetailErrorCount);
}

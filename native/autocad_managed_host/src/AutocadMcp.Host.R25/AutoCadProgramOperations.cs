using System.Globalization;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using Document = Autodesk.AutoCAD.ApplicationServices.Document;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadProgramOperations(
    AutoCadIdleScheduler scheduler,
    DocumentIdentityRegistry identities,
    string packageHash)
{
    private readonly CadHostBinding _host = new(
        "managed_dotnet",
        HostConstants.HostFamily,
        HostConstants.HostVersion,
        HostConstants.PackageId,
        HostConstants.PackageVersion,
        packageHash);
    private readonly CadPreviewLedger _previews = new();

    public Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken) =>
        scheduler.RunAsync<object>(() => Execute(command), cancellationToken);

    private object Execute(CommandRequest command)
    {
        if (!CadProgramV02Contract.OperationIds.Contains(command.OperationId))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "CAD Program operation is not registered.");
        }
        CadHostAdmission.AssertCommandState(GetCommandActive());

        var request = CadProgramV02Parser.ParseRequest(command.OperationId, command.Arguments);
        CadProgramV02Parser.AssertHostBinding(request.ExecutionBinding, _host);
        CadHostAdmission.AssertDeadline(
            command.DeadlineAt,
            DateTimeOffset.UtcNow,
            request.Program?.Budgets.ExecutionDeadlineSeconds ??
            CadProgramV02Contract.MaxDeadlineSeconds);
        var document = Application.DocumentManager.MdiActiveDocument
            ?? throw new ProtocolValidationException(
                "no_active_document",
                "No active drawing is open.");
        var documentIdentity = identities.Get(document);
        if (documentIdentity.DatabaseFingerprint == "unavailable")
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Managed write requires a DWG with a stable database fingerprint.");
        }
        var documentId = documentIdentity.DocumentId;
        CadHostAdmission.AssertDocument(
            command.DocumentId,
            request.ExecutionBinding.DocumentId,
            documentId,
            request.Program?.DocumentId);

        return command.OperationId switch
        {
            "cad.program.preview" => Preview(
                document,
                command.CommandId,
                RequireProgram(request),
                request.ExecutionBinding),
            "cad.program.commit" => Commit(
                document,
                RequireProgram(request),
                request.ExecutionBinding,
                request.Preview ?? throw new ProtocolValidationException(
                    "preview_required",
                    "Commit requires an exact preview binding.")),
            "cad.program.validate" => Validate(
                document,
                request.ExecutionBinding,
                request.Validation ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "Validate requires a bounded validation request.")),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "CAD Program operation is not registered.")
        };
    }

    private object Preview(
        Document document,
        string previewId,
        CadProgramV02 program,
        CadExecutionBinding binding)
    {
        var identity = identities.Get(document);
        var revisionBefore = Revision(identity);
        AssertRevision(binding.DocumentRevision, revisionBefore);
        AssertRevision(program.ExpectedDocumentRevision, revisionBefore);
        var plan = CadProgramPlan.Build(program);
        AssertPostconditions(program, plan);
        ApplyResult applied;

        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartTransaction())
        {
            applied = Apply(document.Database, transaction, program);
            transaction.Abort();
        }

        var revisionAfter = Revision(identity);
        if (revisionAfter != revisionBefore)
        {
            throw new ProtocolValidationException(
                "preview_abort_failed",
                "Preview transaction changed the drawing revision after abort.");
        }
        var previewDigest = CadProgramV02Parser.BuildPreviewDigest(previewId, program, binding);
        var expiresAt = DateTimeOffset.UtcNow.AddSeconds(program.Budgets.PreviewTtlSeconds);
        _previews.Add(new CadPreviewRecord(
            previewId,
            previewDigest,
            program.ProgramDigest,
            previewDigest,
            expiresAt));
        return new
        {
            preview_id = previewId,
            preview_digest = previewDigest,
            expires_at = expiresAt.ToString("O"),
            planned_operation_count = program.Operations.Count,
            planned_entity_count = applied.Entities.Count,
            planned_layer_count = applied.EnsuredLayers.Count,
            transaction_aborted = true,
            drawing_unchanged = true
        };
    }

    private object Commit(
        Document document,
        CadProgramV02 program,
        CadExecutionBinding binding,
        CadPreviewReference preview)
    {
        var identity = identities.Get(document);
        var idempotencyKey = preview.PreviewId;
        using (document.LockDocument())
        using (var duplicateTransaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var existing = DrawingProgramLedger.FindV02(
                document.Database,
                duplicateTransaction,
                idempotencyKey);
            if (existing is not null)
            {
                AssertDuplicate(existing, program, binding);
                return CommitResult(existing, duplicate: true);
            }
        }
        var expectedPreviewDigest = CadProgramV02Parser.BuildPreviewDigest(
            preview.PreviewId,
            program,
            binding);
        _previews.Require(
            preview,
            program.ProgramDigest,
            expectedPreviewDigest,
            DateTimeOffset.UtcNow);

        var revisionBefore = Revision(identity);
        AssertRevision(binding.DocumentRevision, revisionBefore);
        AssertRevision(program.ExpectedDocumentRevision, revisionBefore);
        var revisionAfter = (long.Parse(revisionBefore, CultureInfo.InvariantCulture) + 1)
            .ToString(CultureInfo.InvariantCulture);
        DurableProgramReceiptV02 receipt;

        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartTransaction())
        {
            var existing = DrawingProgramLedger.FindV02(
                document.Database,
                transaction,
                idempotencyKey);
            if (existing is not null)
            {
                AssertDuplicate(existing, program, binding);
                return CommitResult(existing, duplicate: true);
            }

            var applied = Apply(document.Database, transaction, program);
            receipt = new DurableProgramReceiptV02(
                idempotencyKey,
                program.ProgramDigest,
                binding.ExecutionDigest,
                program.DocumentId,
                revisionBefore,
                revisionAfter,
                applied.Entities.Select(EntityEvidence).ToArray(),
                applied.EnsuredLayers);
            DrawingProgramLedger.AddV02(document.Database, transaction, receipt);
            transaction.Commit();
        }

        identity.Revision.Record(
            DocumentEventKind.ObjectModified,
            DateTimeOffset.UtcNow,
            receipt.ReceiptId,
            changesContent: true);
        var actualAfter = Revision(identity);
        if (actualAfter != revisionAfter)
        {
            throw new ProtocolValidationException(
                "commit_validation_failed",
                "Committed drawing revision did not match the durable receipt.");
        }
        return CommitResult(receipt, duplicate: false);
    }

    private object Validate(
        Document document,
        CadExecutionBinding binding,
        CadValidationRequest request)
    {
        var identity = identities.Get(document);
        var revision = Revision(identity);
        AssertRevision(binding.DocumentRevision, revision);
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var receipt = DrawingProgramLedger.FindByReceiptIdV02(
                document.Database,
                transaction,
                request.ReceiptId)
                ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "CAD Program receipt was not found in the active drawing.");
            if (receipt.DocumentId != binding.DocumentId ||
                receipt.ProgramDigest != binding.ProgramDigest)
            {
                throw new ProtocolValidationException(
                    "document_changed",
                    "Receipt does not match the validation binding.");
            }

            var actual = receipt.Entities
                .Select(item => ReadEntity(document.Database, transaction, item))
                .ToArray();
            var failures = new List<string>();
            var checks = new List<string>
            {
                "receipt_binding",
                "entity_handles",
                "entity_types",
                "layers",
                "bounds",
                "document_revision"
            };
            if (request.ExpectedEntityCount is int count && actual.Length != count)
            {
                failures.Add("entity_count_mismatch");
            }
            var actualTypes = actual.Select(item => item.EntityType)
                .ToHashSet(StringComparer.Ordinal);
            foreach (var expectedType in request.ExpectedEntityTypes)
            {
                if (!actualTypes.Contains(expectedType))
                {
                    failures.Add($"missing_entity_type:{expectedType}");
                }
            }
            var actualLayers = actual.Select(item => item.Layer)
                .Concat(receipt.Layers)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            foreach (var expectedLayer in request.ExpectedLayers)
            {
                if (!actualLayers.Contains(expectedLayer))
                {
                    failures.Add($"missing_layer:{expectedLayer}");
                }
            }
            return new
            {
                validation_id = $"validation-{Guid.NewGuid():N}",
                valid = failures.Count == 0,
                document_revision = revision,
                checks,
                failures
            };
        }
    }

    private static ApplyResult Apply(
        Database database,
        Transaction transaction,
        CadProgramV02 program)
    {
        var blockTable = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead);
        var modelSpace = (BlockTableRecord)transaction.GetObject(
            blockTable[BlockTableRecord.ModelSpace],
            OpenMode.ForWrite);
        var entities = new List<Entity>();
        var ensuredLayers = new List<string>();
        foreach (var operation in program.Operations)
        {
            switch (operation)
            {
                case EnsureLayerOperation layer:
                    EnsureLayer(database, transaction, layer.Name, layer.ColorIndex);
                    ensuredLayers.Add(layer.Name);
                    break;
                case CreateLineOperation line:
                    RequireLayer(database, transaction, line.Layer);
                    entities.Add(AddEntity(
                        modelSpace,
                        transaction,
                        new Line(ToPoint(line.Start), ToPoint(line.End))
                        {
                            Layer = line.Layer
                        }));
                    break;
                case CreateCircleOperation circle:
                    RequireLayer(database, transaction, circle.Layer);
                    entities.Add(AddEntity(
                        modelSpace,
                        transaction,
                        new Circle(ToPoint(circle.Center), Vector3d.ZAxis, circle.Radius)
                        {
                            Layer = circle.Layer
                        }));
                    break;
                case CreatePolylineOperation polyline:
                    RequireLayer(database, transaction, polyline.Layer);
                    entities.Add(AddEntity(
                        modelSpace,
                        transaction,
                        new Polyline3d(
                            Poly3dType.SimplePoly,
                            new Point3dCollection(polyline.Vertices.Select(ToPoint).ToArray()),
                            polyline.Closed)
                        {
                            Layer = polyline.Layer
                        }));
                    break;
                case CreateRectangleOperation rectangle:
                    RequireLayer(database, transaction, rectangle.Layer);
                    entities.Add(AddEntity(
                        modelSpace,
                        transaction,
                        Rectangle(rectangle)));
                    break;
                case CreateTextOperation text:
                    RequireLayer(database, transaction, text.Layer);
                    entities.Add(AddEntity(
                        modelSpace,
                        transaction,
                        new DBText
                        {
                            Layer = text.Layer,
                            Position = ToPoint(text.Position),
                            TextString = text.Text,
                            Height = text.Height,
                            Rotation = text.RotationRadians
                        }));
                    break;
                case CreateDimensionLinearOperation dimension:
                    RequireLayer(database, transaction, dimension.Layer);
                    entities.Add(AddEntity(
                        modelSpace,
                        transaction,
                        Dimension(database, dimension)));
                    break;
                default:
                    throw new ProtocolValidationException(
                        "capability_missing",
                        "CAD Program operation is outside the exact create-only registry.");
            }
        }
        return new ApplyResult(entities, ensuredLayers.Distinct(StringComparer.OrdinalIgnoreCase).ToArray());
    }

    private static Entity Rectangle(CreateRectangleOperation operation)
    {
        var first = operation.FirstCorner;
        var opposite = operation.OppositeCorner;
        var polyline = new Polyline(4)
        {
            Layer = operation.Layer,
            Elevation = first.Z,
            Closed = true
        };
        polyline.AddVertexAt(0, new Point2d(first.X, first.Y), 0, 0, 0);
        polyline.AddVertexAt(1, new Point2d(opposite.X, first.Y), 0, 0, 0);
        polyline.AddVertexAt(2, new Point2d(opposite.X, opposite.Y), 0, 0, 0);
        polyline.AddVertexAt(3, new Point2d(first.X, opposite.Y), 0, 0, 0);
        return polyline;
    }

    private static Entity Dimension(
        Database database,
        CreateDimensionLinearOperation operation)
    {
        var first = ToPoint(operation.ExtensionLine1Point);
        var second = ToPoint(operation.ExtensionLine2Point);
        var rotation = Math.Atan2(second.Y - first.Y, second.X - first.X);
        return new RotatedDimension(
            rotation,
            first,
            second,
            ToPoint(operation.DimensionLinePoint),
            operation.TextOverride ?? string.Empty,
            database.Dimstyle)
        {
            Layer = operation.Layer
        };
    }

    private static void EnsureLayer(
        Database database,
        Transaction transaction,
        string name,
        short? colorIndex)
    {
        var layers = (LayerTable)transaction.GetObject(
            database.LayerTableId,
            OpenMode.ForRead);
        if (layers.Has(name))
        {
            return;
        }
        layers.UpgradeOpen();
        var layer = new LayerTableRecord { Name = name };
        if (colorIndex is not null)
        {
            layer.Color = Color.FromColorIndex(ColorMethod.ByAci, colorIndex.Value);
        }
        layers.Add(layer);
        transaction.AddNewlyCreatedDBObject(layer, true);
    }

    private static void RequireLayer(
        Database database,
        Transaction transaction,
        string name)
    {
        var layers = (LayerTable)transaction.GetObject(
            database.LayerTableId,
            OpenMode.ForRead);
        if (!layers.Has(name))
        {
            throw new ProtocolValidationException(
                "program_invalid",
                $"Layer '{name}' does not exist; add ensure_layer before using it.");
        }
    }

    private static T AddEntity<T>(
        BlockTableRecord modelSpace,
        Transaction transaction,
        T entity)
        where T : Entity
    {
        modelSpace.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
        if (entity is Dimension dimension)
        {
            dimension.RecomputeDimensionBlock(true);
        }
        return entity;
    }

    private static DurableEntityEvidence EntityEvidence(Entity entity)
    {
        var extents = entity.GeometricExtents;
        return new DurableEntityEvidence(
            entity.Handle.ToString(),
            entity.GetRXClass().DxfName,
            entity.Layer,
            new CadBounds(
                extents.MinPoint.X,
                extents.MinPoint.Y,
                extents.MinPoint.Z,
                extents.MaxPoint.X,
                extents.MaxPoint.Y,
                extents.MaxPoint.Z));
    }

    private static DurableEntityEvidence ReadEntity(
        Database database,
        Transaction transaction,
        DurableEntityEvidence expected)
    {
        ObjectId objectId;
        try
        {
            objectId = database.GetObjectId(
                false,
                new Handle(long.Parse(expected.Handle, NumberStyles.HexNumber, CultureInfo.InvariantCulture)),
                0);
        }
        catch (Autodesk.AutoCAD.Runtime.Exception)
        {
            throw new ProtocolValidationException(
                "document_changed",
                "A committed CAD Program entity no longer exists.");
        }
        if (transaction.GetObject(objectId, OpenMode.ForRead) is not Entity entity)
        {
            throw new ProtocolValidationException(
                "document_changed",
                "A committed CAD Program object is no longer an entity.");
        }
        var actual = EntityEvidence(entity);
        if (actual.EntityType != expected.EntityType ||
            !string.Equals(actual.Layer, expected.Layer, StringComparison.OrdinalIgnoreCase) ||
            !BoundsEqual(actual.Bounds, expected.Bounds))
        {
            throw new ProtocolValidationException(
                "document_changed",
                "A committed CAD Program entity changed type, layer, or bounds.");
        }
        return actual;
    }

    private static bool BoundsEqual(CadBounds left, CadBounds right)
    {
        const double tolerance = 1e-8;
        return Math.Abs(left.MinX - right.MinX) <= tolerance &&
               Math.Abs(left.MinY - right.MinY) <= tolerance &&
               Math.Abs(left.MinZ - right.MinZ) <= tolerance &&
               Math.Abs(left.MaxX - right.MaxX) <= tolerance &&
               Math.Abs(left.MaxY - right.MaxY) <= tolerance &&
               Math.Abs(left.MaxZ - right.MaxZ) <= tolerance;
    }

    private static object CommitResult(DurableProgramReceiptV02 receipt, bool duplicate) => new
    {
        receipt_id = receipt.ReceiptId,
        receipt_digest = receipt.ReceiptDigest,
        document_revision_before = receipt.DocumentRevisionBefore,
        document_revision_after = receipt.DocumentRevisionAfter,
        created_entity_count = receipt.Entities.Count,
        duplicate
    };

    private static void AssertDuplicate(
        DurableProgramReceiptV02 receipt,
        CadProgramV02 program,
        CadExecutionBinding binding)
    {
        if (receipt.ProgramDigest != program.ProgramDigest ||
            receipt.ExecutionDigest != binding.ExecutionDigest ||
            receipt.DocumentId != program.DocumentId)
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "Idempotency key was reused with a conflicting CAD Program binding.");
        }
    }

    private static void AssertPostconditions(CadProgramV02 program, CadProgramPlan plan)
    {
        foreach (var condition in program.Postconditions)
        {
            if (condition is EntityCountPostcondition count &&
                count.ExpectedCreated != plan.Entities.Count)
            {
                throw new ProtocolValidationException(
                    "program_invalid",
                    "Entity count postcondition does not match the create plan.");
            }
        }
    }

    private static CadProgramV02 RequireProgram(CadProgramV02Request request) =>
        request.Program ?? throw new ProtocolValidationException(
            "program_invalid",
            "CAD Program payload is required.");

    private static string Revision(DocumentIdentity identity) =>
        identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision
            .ToString(CultureInfo.InvariantCulture);

    private static void AssertRevision(string expected, string actual)
    {
        if (expected != actual)
        {
            throw new ProtocolValidationException(
                "document_changed",
                "The drawing revision does not match the exact CAD Program binding.");
        }
    }

    private static Point3d ToPoint(CadPoint point) => new(point.X, point.Y, point.Z);

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

    private sealed record ApplyResult(
        IReadOnlyList<Entity> Entities,
        IReadOnlyList<string> EnsuredLayers);
}

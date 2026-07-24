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
    private readonly CadRuntimeBinding _runtime = new(
        "managed_dotnet",
        HostConstants.HostFamily,
        HostConstants.HostVersion,
        packageHash);

    public Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken) =>
        scheduler.RunAsync<object>(() => Execute(command), cancellationToken);

    private object Execute(CommandRequest command)
    {
        if (!CadProgramContract.OperationIds.Contains(command.OperationId))
        {
            throw new ProtocolValidationException("capability_missing", "CAD Program operation is not registered.");
        }
        if (GetCommandActive() != 0)
        {
            throw new ProtocolValidationException("autocad_busy", "AutoCAD is executing another command.");
        }

        var request = CadProgramParser.ParseRequest(command.OperationId, command.Arguments);
        CadProgramParser.AssertRuntime(request.Program.RuntimeBinding, _runtime);
        var document = Application.DocumentManager.MdiActiveDocument
            ?? throw new ProtocolValidationException("no_active_document", "No active drawing is open.");
        var documentId = identities.Get(document).DocumentId;
        var documentMatches =
            (command.DocumentId is null || command.DocumentId == documentId) &&
            request.Program.DocumentId == documentId;
        if (command.OperationId != "cad.program.commit" && !documentMatches)
        {
            throw new ProtocolValidationException("active_document_changed", "The active document changed.");
        }

        return command.OperationId switch
        {
            "cad.program.preview" => Preview(document, request.Program),
            "cad.program.commit" => Commit(document, request, documentMatches),
            "cad.program.validate" => Validate(document, request.Program),
            _ => throw new ProtocolValidationException("capability_missing", "CAD Program operation is not registered.")
        };
    }

    private object Preview(Document document, CadProgram program)
    {
        var identity = identities.Get(document);
        var documentBefore = identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision;
        AssertRevision(program.ExpectedRevision, documentBefore);
        var executionDigest = CadProgramParser.BuildExecutionDigest(program, _runtime);
        int createdEntityCount;
        int createdLayerCount;

        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartTransaction())
        {
            (createdEntityCount, createdLayerCount) = Apply(document.Database, transaction, program);
            transaction.Abort();
        }

        var documentAfter = identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision;
        if (documentAfter != documentBefore)
        {
            throw new ProtocolValidationException(
                "preview_abort_failed",
                "Preview transaction changed the drawing after abort.");
        }

        return new
        {
            status = "previewed",
            program_digest = program.ProgramDigest,
            execution_digest = executionDigest,
            document_before = documentBefore,
            document_after = documentAfter,
            preview_strategy = "database_transaction_abort",
            revision_strength = "database_object_fingerprint",
            planned_operation_count = program.Operations.Count,
            planned_entity_count = createdEntityCount,
            planned_layer_count = createdLayerCount,
            runtime_binding = _runtime,
            validation = new
            {
                transaction_aborted = true,
                drawing_unchanged = true,
                bounds_valid = true,
                operation_allowlist_valid = true
            }
        };
    }

    private object Commit(
        Document document,
        CadProgramRequest request,
        bool documentMatches)
    {
        var program = request.Program;
        var preview = request.Preview
            ?? throw new ProtocolValidationException("preview_required", "Commit requires an exact preview binding.");
        CadProgramParser.AssertRuntime(preview.RuntimeBinding, _runtime);
        var executionDigest = CadProgramParser.BuildExecutionDigest(program, _runtime);
        if (preview.ExecutionDigest != executionDigest)
        {
            throw new ProtocolValidationException(
                "runtime_changed",
                "Execution plan changed after preview.");
        }

        var identity = identities.Get(document);
        long documentBefore;
        int createdEntityCount;
        int createdLayerCount;
        var checkpointId = Checkpoint(executionDigest, program.IdempotencyKey);
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartTransaction())
        {
            var existing = DrawingProgramLedger.Find(
                document.Database,
                transaction,
                program.IdempotencyKey);
            if (existing is not null)
            {
                if (existing.ProgramDigest != program.ProgramDigest)
                {
                    throw new ProtocolValidationException(
                        "duplicate_payload_mismatch",
                        "Idempotency key was reused for another CAD Program.");
                }
                return new
                {
                    status = "duplicate",
                    program_digest = existing.ProgramDigest,
                    execution_digest = existing.ExecutionDigest,
                    document_after = identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision,
                    checkpoint_id = existing.CheckpointId,
                    effect_applied = false,
                    duplicate_of_succeeded_commit = true,
                    durable_receipt = true,
                    runtime_binding = _runtime
                };
            }
            if (!documentMatches)
            {
                throw new ProtocolValidationException(
                    "active_document_changed",
                    "The active document changed and has no matching durable commit receipt.");
            }

            documentBefore = identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision;
            AssertRevision(program.ExpectedRevision, documentBefore);
            if (preview.DocumentRevision != documentBefore)
            {
                throw new ProtocolValidationException(
                    "document_changed",
                    "The drawing changed after preview.");
            }
            (createdEntityCount, createdLayerCount) = Apply(document.Database, transaction, program);
            DrawingProgramLedger.Add(
                document.Database,
                transaction,
                new DurableProgramReceipt(
                    program.IdempotencyKey,
                    program.ProgramDigest,
                    executionDigest,
                    checkpointId));
            transaction.Commit();
        }

        var documentAfter = identity.Revision.Snapshot(DateTimeOffset.UtcNow).Revision;
        var effectApplied = createdEntityCount > 0 || createdLayerCount > 0;
        if (effectApplied && documentAfter == documentBefore)
        {
            throw new ProtocolValidationException(
                "commit_validation_failed",
                "Commit completed without an observable database revision.");
        }
        return new
        {
            status = "committed",
            program_digest = program.ProgramDigest,
            execution_digest = executionDigest,
            document_before = documentBefore,
            document_after = documentAfter,
            checkpoint_id = checkpointId,
            durable_receipt = true,
            effect_applied = effectApplied,
            created_entity_count = createdEntityCount,
            created_layer_count = createdLayerCount,
            preview_strategy = "database_transaction_abort",
            revision_strength = "database_object_fingerprint",
            runtime_binding = _runtime,
            validation = new
            {
                revision_advanced = documentAfter > documentBefore,
                transaction_committed = true,
                operation_count = program.Operations.Count
            }
        };
    }

    private object Validate(Document document, CadProgram program)
    {
        var revision = identities.Get(document).Revision.Snapshot(DateTimeOffset.UtcNow).Revision;
        AssertRevision(program.ExpectedRevision, revision);
        return new
        {
            status = "validated",
            program_digest = program.ProgramDigest,
            execution_digest = CadProgramParser.BuildExecutionDigest(program, _runtime),
            document_revision = revision,
            runtime_binding = _runtime,
            validation = new
            {
                bounds_valid = true,
                operation_allowlist_valid = true,
                runtime_binding_valid = true,
                document_revision_valid = true
            }
        };
    }

    private static (int EntityCount, int LayerCount) Apply(
        Database database,
        Transaction transaction,
        CadProgram program)
    {
        var blockTable = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
        var modelSpace = (BlockTableRecord)transaction.GetObject(
            blockTable[BlockTableRecord.ModelSpace],
            OpenMode.ForWrite);
        var entityCount = 0;
        var layerCount = 0;

        foreach (var operation in program.Operations)
        {
            switch (operation)
            {
                case EnsureLayerOperation layer:
                    layerCount += EnsureLayer(database, transaction, layer.Name, layer.ColorIndex) ? 1 : 0;
                    break;
                case CreateLineOperation line:
                    RequireLayer(database, transaction, line.Layer);
                    AddEntity(
                        modelSpace,
                        transaction,
                        new Line(ToPoint(line.Start), ToPoint(line.End)) { Layer = line.Layer });
                    entityCount++;
                    break;
                case CreateCircleOperation circle:
                    RequireLayer(database, transaction, circle.Layer);
                    AddEntity(
                        modelSpace,
                        transaction,
                        new Circle(ToPoint(circle.Center), Vector3d.ZAxis, circle.Radius)
                        {
                            Layer = circle.Layer
                        });
                    entityCount++;
                    break;
                case CreatePolylineOperation polyline:
                    RequireLayer(database, transaction, polyline.Layer);
                    AddEntity(
                        modelSpace,
                        transaction,
                        new Polyline3d(
                            Poly3dType.SimplePoly,
                            new Point3dCollection(polyline.Vertices.Select(ToPoint).ToArray()),
                            polyline.Closed)
                        {
                            Layer = polyline.Layer
                        });
                    entityCount++;
                    break;
                case CreateTextOperation text:
                    RequireLayer(database, transaction, text.Layer);
                    AddEntity(
                        modelSpace,
                        transaction,
                        new DBText
                        {
                            Layer = text.Layer,
                            Position = ToPoint(text.Position),
                            TextString = text.Text,
                            Height = text.Height,
                            Rotation = text.RotationRadians
                        });
                    entityCount++;
                    break;
                default:
                    throw new ProtocolValidationException(
                        "capability_missing",
                        "CAD Program operation is not in the create-only allowlist.");
            }
        }
        return (entityCount, layerCount);
    }

    private static bool EnsureLayer(
        Database database,
        Transaction transaction,
        string name,
        short? colorIndex)
    {
        var layers = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        if (layers.Has(name))
        {
            return false;
        }
        layers.UpgradeOpen();
        var layer = new LayerTableRecord { Name = name };
        if (colorIndex is not null)
        {
            layer.Color = Color.FromColorIndex(ColorMethod.ByAci, colorIndex.Value);
        }
        layers.Add(layer);
        transaction.AddNewlyCreatedDBObject(layer, true);
        return true;
    }

    private static void RequireLayer(Database database, Transaction transaction, string name)
    {
        var layers = (LayerTable)transaction.GetObject(database.LayerTableId, OpenMode.ForRead);
        if (!layers.Has(name))
        {
            throw new ProtocolValidationException(
                "program_invalid",
                $"Layer '{name}' does not exist; add ensure_layer before using it.");
        }
    }

    private static void AddEntity(
        BlockTableRecord modelSpace,
        Transaction transaction,
        Entity entity)
    {
        modelSpace.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
    }

    private static string Checkpoint(string executionDigest, string idempotencyKey)
    {
        using var source = System.Text.Json.JsonDocument.Parse(
            $$"""{"execution_digest":"{{executionDigest}}","idempotency_key":"{{idempotencyKey}}"}""");
        return $"checkpoint-{CanonicalJson.Hash(source.RootElement)[..24]}";
    }

    private static void AssertRevision(long expected, long actual)
    {
        if (expected != actual)
        {
            throw new ProtocolValidationException(
                "document_changed",
                "The drawing revision does not match the CAD Program.");
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
}

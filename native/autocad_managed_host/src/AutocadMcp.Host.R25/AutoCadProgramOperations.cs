using System.Globalization;
using System.Text.Json.Nodes;
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
    private readonly Dictionary<string, RollbackPreviewRecord> _rollbackPreviews =
        new(StringComparer.Ordinal);

    public Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken) =>
        scheduler.RunAsync<object>(() => Execute(command), cancellationToken);

    private object Execute(CommandRequest command)
    {
        if (Phase7RollbackContract.OperationIds.Contains(command.OperationId))
        {
            return ExecuteRollback(command);
        }
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
                request.PreviewId ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "Preview requires the Gateway preview ID."),
                request.PreviewExpiresAt ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "Preview requires the Gateway expiry."),
                RequireProgram(request),
                request.ExecutionBinding),
            "cad.program.commit" => Commit(
                document,
                RequireProgram(request),
                request.ExecutionBinding,
                request.Preview ?? throw new ProtocolValidationException(
                    "preview_required",
                    "Commit requires an exact preview binding."),
                request.ReceiptId ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "Commit requires the exact durable receipt ID.")),
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

    private object ExecuteRollback(CommandRequest command)
    {
        CadHostAdmission.AssertCommandState(GetCommandActive());
        CadHostAdmission.AssertDeadline(
            command.DeadlineAt,
            DateTimeOffset.UtcNow,
            CadProgramV02Contract.MaxDeadlineSeconds);
        var request = Phase7RollbackParser.Parse(command.OperationId, command.Arguments);
        var document = Application.DocumentManager.MdiActiveDocument
            ?? throw new ProtocolValidationException(
                "no_active_document",
                "No active drawing is open.");
        var identity = identities.Get(document);
        CadHostAdmission.AssertDocument(
            command.DocumentId,
            command.DocumentId ?? "",
            identity.DocumentId,
            null);
        return command.OperationId switch
        {
            "cad.recovery.receipt_query" => QueryReceipt(
                document,
                request.ReceiptId!),
            "cad.rollback.checkpoint.lookup" => QueryCheckpoint(
                document,
                request.CheckpointId!),
            "cad.rollback.preview" => PreviewRollback(document, request),
            "cad.rollback.commit" => CommitRollback(document, request),
            "cad.rollback.validate" => ValidateRollback(
                document,
                request.RollbackReceiptId!),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "Rollback operation is not registered.")
        };
    }

    private object QueryReceipt(Document document, string receiptId)
    {
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var receipt = DrawingProgramLedger.FindByReceiptIdV02(
                document.Database,
                transaction,
                receiptId);
            if (receipt is null)
            {
                return new { found = false, receipt_id = receiptId, rollback_eligible = false };
            }
            var checkpointId = CadRollbackCheckpointV1.BuildCheckpointId(receipt.ReceiptId);
            var checkpoint = DrawingProgramLedger.FindCheckpoint(
                document.Database,
                transaction,
                checkpointId);
            return new
            {
                found = true,
                receipt_id = receipt.ReceiptId,
                receipt_digest = receipt.ReceiptDigest,
                execution_digest = receipt.ExecutionDigest,
                document_id = receipt.DocumentId,
                document_revision_after = receipt.DocumentRevisionAfter,
                rollback_eligible = checkpoint is not null,
                checkpoint_id = checkpoint?.CheckpointId,
                checkpoint_digest = checkpoint?.CheckpointDigest
            };
        }
    }

    private object QueryCheckpoint(Document document, string checkpointId)
    {
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var checkpoint = DrawingProgramLedger.FindCheckpoint(
                document.Database,
                transaction,
                checkpointId);
            return checkpoint is null
                ? new { found = false, checkpoint_id = checkpointId }
                : new
                {
                    found = true,
                    checkpoint_id = checkpoint.CheckpointId,
                    checkpoint_digest = checkpoint.CheckpointDigest,
                    original_receipt_id = checkpoint.OriginalReceiptId,
                    original_receipt_digest = checkpoint.OriginalReceiptDigest,
                    program_id = checkpoint.ProgramId,
                    program_revision = checkpoint.ProgramRevision,
                    program_digest = checkpoint.ProgramDigest,
                    preview_id = checkpoint.PreviewId,
                    preview_digest = checkpoint.PreviewDigest,
                    execution_digest = checkpoint.ExecutionDigest,
                    document_id = checkpoint.DocumentId,
                    document_revision_before = checkpoint.DocumentRevisionBefore,
                    document_revision_after = checkpoint.DocumentRevisionAfter,
                    created_entities = checkpoint.CreatedEntities,
                    non_entity_object_created = checkpoint.NonEntityObjectCreated,
                    runtime_pins = RuntimePins(checkpoint),
                    policy_pins = PolicyPins(checkpoint),
                    created_at = checkpoint.CreatedAt
                };
        }
    }

    private object PreviewRollback(Document document, CadRollbackRequest request)
    {
        var identity = identities.Get(document);
        var revision = Revision(identity);
        var conflicts = new List<object>();
        CadRollbackCheckpointV1? checkpoint;
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            checkpoint = DrawingProgramLedger.FindCheckpoint(
                document.Database,
                transaction,
                request.CheckpointId!);
            if (checkpoint is null)
            {
                conflicts.Add(Conflict("checkpoint_missing", null, "Phase 7 checkpoint is absent."));
            }
            else if (checkpoint.CheckpointDigest != request.CheckpointDigest)
            {
                conflicts.Add(Conflict("evidence_mismatch", null, "Checkpoint digest changed."));
            }
            else
            {
                AssertCheckpointPins(checkpoint);
                if (checkpoint.DocumentId != identity.DocumentId)
                {
                    conflicts.Add(Conflict(
                        "document_identity_mismatch",
                        null,
                        "Active drawing identity differs from the checkpoint."));
                }
                if (checkpoint.DocumentRevisionAfter != revision)
                {
                    conflicts.Add(Conflict(
                        "document_revision_mismatch",
                        null,
                        "Drawing revision differs from the checkpoint revision."));
                }
                if (conflicts.Count == 0)
                {
                    foreach (var expected in checkpoint.CreatedEntities)
                    {
                        InspectRollbackEntity(
                            document.Database,
                            transaction,
                            expected,
                            conflicts);
                    }
                }
            }
        }
        var eligible = checkpoint is not null && conflicts.Count == 0;
        _rollbackPreviews[request.RollbackPlanId!] = new RollbackPreviewRecord(
            request.RollbackPlanId!,
            request.CheckpointId!,
            request.CheckpointDigest!,
            request.RollbackExecutionDigest!,
            request.ExpiresAt!,
            revision,
            eligible);
        return new
        {
            checkpoint_id = request.CheckpointId,
            checkpoint_digest = request.CheckpointDigest,
            current_document_revision = revision,
            eligible,
            conflicts,
            milestone = "transaction_aborted",
            runtime_pins = checkpoint is null ? null : RuntimePins(checkpoint),
            policy_pins = checkpoint is null ? null : PolicyPins(checkpoint)
        };
    }

    private object CommitRollback(Document document, CadRollbackRequest request)
    {
        var identity = identities.Get(document);
        using (document.LockDocument())
        using (var duplicateTransaction =
            document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var existing = DrawingProgramLedger.FindRollbackReceipt(
                document.Database,
                duplicateTransaction,
                request.RollbackReceiptId!);
            if (existing is not null)
            {
                AssertRollbackDuplicate(existing, request);
                return RollbackCommitResult(existing, duplicate: true);
            }
        }
        if (!_rollbackPreviews.TryGetValue(request.RollbackPlanId!, out var preview) ||
            preview.CheckpointId != request.CheckpointId ||
            preview.CheckpointDigest != request.CheckpointDigest ||
            preview.RollbackExecutionDigest != request.RollbackExecutionDigest ||
            preview.ExpiresAt != request.ExpiresAt ||
            !preview.Eligible ||
            DateTimeOffset.Parse(preview.ExpiresAt) <= DateTimeOffset.UtcNow)
        {
            throw new ProtocolValidationException(
                "preview_required",
                "Exact clean rollback preview is missing, changed, or expired.");
        }
        var revisionBefore = Revision(identity);
        if (revisionBefore != preview.DocumentRevision)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Drawing revision changed after rollback preview.");
        }
        var revisionAfter = (long.Parse(revisionBefore, CultureInfo.InvariantCulture) + 1)
            .ToString(CultureInfo.InvariantCulture);
        DurableRollbackReceiptV1 receipt;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartTransaction())
        {
            var duplicate = DrawingProgramLedger.FindRollbackReceipt(
                document.Database,
                transaction,
                request.RollbackReceiptId!);
            if (duplicate is not null)
            {
                AssertRollbackDuplicate(duplicate, request);
                return RollbackCommitResult(duplicate, duplicate: true);
            }
            var checkpoint = DrawingProgramLedger.FindCheckpoint(
                document.Database,
                transaction,
                request.CheckpointId!)
                ?? throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Checkpoint disappeared before rollback commit.");
            if (checkpoint.CheckpointDigest != request.CheckpointDigest ||
                checkpoint.DocumentRevisionAfter != revisionBefore)
            {
                throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Checkpoint or document revision changed before rollback commit.");
            }
            AssertCheckpointPins(checkpoint);
            var removed = new List<CadRemovedEntityEvidence>();
            foreach (var expected in checkpoint.CreatedEntities)
            {
                var entity = RequireRollbackEntity(
                    document.Database,
                    transaction,
                    expected);
                removed.Add(new(
                    expected.Handle,
                    expected.EntityType,
                    expected.CanonicalFingerprint));
                entity.UpgradeOpen();
                entity.Erase();
            }
            receipt = DurableRollbackReceiptV1.Create(
                request.RollbackReceiptId!,
                checkpoint,
                request.RollbackPlanId!,
                request.RollbackPlanDigest!,
                request.RollbackExecutionDigest!,
                revisionBefore,
                revisionAfter,
                removed,
                DateTimeOffset.UtcNow);
            DrawingProgramLedger.AddRollbackReceipt(
                document.Database,
                transaction,
                receipt);
            transaction.Commit();
        }
        identity.Revision.Record(
            DocumentEventKind.ObjectErased,
            DateTimeOffset.UtcNow,
            receipt.RollbackReceiptId,
            changesContent: true);
        if (Revision(identity) != revisionAfter)
        {
            throw new ProtocolValidationException(
                "commit_validation_failed",
                "Rollback drawing revision did not match its durable receipt.");
        }
        return RollbackCommitResult(receipt, duplicate: false);
    }

    private object ValidateRollback(Document document, string rollbackReceiptId)
    {
        using (document.LockDocument())
        using (var transaction = document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var receipt = DrawingProgramLedger.FindRollbackReceipt(
                document.Database,
                transaction,
                rollbackReceiptId)
                ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "Rollback receipt was not found.");
            var failures = new List<string>();
            foreach (var removed in receipt.RemovedEntities)
            {
                if (TryObjectId(document.Database, removed.Handle, out _))
                {
                    failures.Add($"entity_still_present:{removed.Handle}");
                }
            }
            return new
            {
                rollback_receipt_id = receipt.RollbackReceiptId,
                valid = failures.Count == 0,
                document_revision = Revision(identities.Get(document)),
                checks = new[] { "rollback_receipt_binding", "entities_absent" },
                failures
            };
        }
    }

    private object Preview(
        Document document,
        string previewId,
        string previewExpiresAt,
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
        var expiresAt = DateTimeOffset.Parse(
            previewExpiresAt,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind);
        if (expiresAt <= DateTimeOffset.UtcNow)
        {
            throw new ProtocolValidationException(
                "preview_expired",
                "The Gateway preview expiry has elapsed.");
        }
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
            expires_at = previewExpiresAt,
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
        CadPreviewReference preview,
        string receiptId)
    {
        var identity = identities.Get(document);
        var idempotencyKey = preview.PreviewId;
        if (receiptId != DurableProgramReceiptV02.BuildReceiptId(preview.PreviewId))
        {
            throw new ProtocolValidationException(
                "program_invalid",
                "Receipt ID does not match the exact preview binding.");
        }
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
                var existingCheckpoint = DrawingProgramLedger.FindCheckpoint(
                    document.Database,
                    duplicateTransaction,
                    CadRollbackCheckpointV1.BuildCheckpointId(existing.ReceiptId));
                return CommitResult(existing, existingCheckpoint, duplicate: true);
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
        CadRollbackCheckpointV1 checkpoint;

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
                var existingCheckpoint = DrawingProgramLedger.FindCheckpoint(
                    document.Database,
                    transaction,
                    CadRollbackCheckpointV1.BuildCheckpointId(existing.ReceiptId));
                return CommitResult(existing, existingCheckpoint, duplicate: true);
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
            checkpoint = CadRollbackCheckpointV1.Create(
                receipt,
                program,
                preview,
                binding,
                applied.Entities.Select(entity => new CadCheckpointEntity(
                    entity.Handle.ToString(),
                    entity.GetRXClass().DxfName,
                    entity.Layer,
                    EntityFingerprint(entity, transaction))).ToArray(),
                applied.CreatedNonEntityObject,
                DateTimeOffset.UtcNow);
            DrawingProgramLedger.AddCheckpoint(
                document.Database,
                transaction,
                checkpoint);
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
        return CommitResult(receipt, checkpoint, duplicate: false);
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
                validation_id = request.ValidationId,
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
        var createdNonEntityObject = false;
        foreach (var operation in program.Operations)
        {
            switch (operation)
            {
                case EnsureLayerOperation layer:
                    createdNonEntityObject |= EnsureLayer(
                        database,
                        transaction,
                        layer.Name,
                        layer.ColorIndex);
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
        return new ApplyResult(
            entities,
            ensuredLayers.Distinct(StringComparer.OrdinalIgnoreCase).ToArray(),
            createdNonEntityObject);
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

    private static bool EnsureLayer(
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

    private static string EntityFingerprint(Entity entity, Transaction transaction)
    {
        var extents = entity.GeometricExtents;
        var geometry = new JsonObject();
        switch (entity)
        {
            case Line line:
                geometry["start"] = Point(line.StartPoint);
                geometry["end"] = Point(line.EndPoint);
                break;
            case Circle circle:
                geometry["center"] = Point(circle.Center);
                geometry["normal"] = Vector(circle.Normal);
                geometry["radius"] = circle.Radius;
                break;
            case Polyline polyline:
                geometry["closed"] = polyline.Closed;
                geometry["elevation"] = polyline.Elevation;
                geometry["vertices"] = new JsonArray(
                    Enumerable.Range(0, polyline.NumberOfVertices)
                        .Select(index =>
                        {
                            var point = polyline.GetPoint2dAt(index);
                            return (JsonNode)new JsonObject
                            {
                                ["x"] = point.X,
                                ["y"] = point.Y,
                                ["bulge"] = polyline.GetBulgeAt(index)
                            };
                        }).ToArray());
                break;
            case Polyline3d polyline3d:
                geometry["closed"] = polyline3d.Closed;
                geometry["vertices"] = new JsonArray(
                    polyline3d.Cast<ObjectId>()
                        .Select(id => transaction.GetObject(id, OpenMode.ForRead))
                        .OfType<PolylineVertex3d>()
                        .Select(vertex => (JsonNode)Point(vertex.Position))
                        .ToArray());
                break;
            case DBText text:
                geometry["position"] = Point(text.Position);
                geometry["text"] = text.TextString;
                geometry["height"] = text.Height;
                geometry["rotation"] = text.Rotation;
                break;
            case Dimension dimension:
                geometry["measurement"] = dimension.Measurement;
                geometry["text"] = dimension.DimensionText;
                geometry["text_position"] = Point(dimension.TextPosition);
                break;
            default:
                throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Entity type has no canonical Phase 7 fingerprint.");
        }
        var value = new JsonObject
        {
            ["entity_type"] = entity.GetRXClass().DxfName,
            ["layer"] = entity.Layer,
            ["linetype"] = entity.Linetype,
            ["linetype_scale"] = entity.LinetypeScale,
            ["lineweight"] = entity.LineWeight.ToString(),
            ["visible"] = entity.Visible,
            ["color_index"] = entity.ColorIndex,
            ["bounds"] = new JsonObject
            {
                ["min"] = Point(extents.MinPoint),
                ["max"] = Point(extents.MaxPoint)
            },
            ["geometry"] = geometry
        };
        using var document = System.Text.Json.JsonDocument.Parse(value.ToJsonString());
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static JsonObject Point(Point3d point) => new()
    {
        ["x"] = point.X,
        ["y"] = point.Y,
        ["z"] = point.Z
    };

    private static JsonObject Vector(Vector3d vector) => new()
    {
        ["x"] = vector.X,
        ["y"] = vector.Y,
        ["z"] = vector.Z
    };

    private static void InspectRollbackEntity(
        Database database,
        Transaction transaction,
        CadCheckpointEntity expected,
        List<object> conflicts)
    {
        if (!TryObjectId(database, expected.Handle, out var objectId))
        {
            conflicts.Add(Conflict(
                "entity_missing",
                expected.Handle,
                "Checkpoint entity is missing."));
            return;
        }
        if (transaction.GetObject(objectId, OpenMode.ForRead) is not Entity entity)
        {
            conflicts.Add(Conflict(
                "entity_type_changed",
                expected.Handle,
                "Checkpoint object is no longer an entity."));
            return;
        }
        if (entity.GetRXClass().DxfName != expected.EntityType)
        {
            conflicts.Add(Conflict(
                "entity_type_changed",
                expected.Handle,
                "Checkpoint entity type changed."));
        }
        else if (!string.Equals(entity.Layer, expected.Layer, StringComparison.OrdinalIgnoreCase))
        {
            conflicts.Add(Conflict(
                "entity_layer_changed",
                expected.Handle,
                "Checkpoint entity layer changed."));
        }
        else if (EntityFingerprint(entity, transaction) != expected.CanonicalFingerprint)
        {
            conflicts.Add(Conflict(
                "entity_fingerprint_changed",
                expected.Handle,
                "Checkpoint entity geometry or properties changed."));
        }
        else if (!DependencySafe(database, transaction, entity))
        {
            conflicts.Add(Conflict(
                "dependency_unproven",
                expected.Handle,
                "Entity dependency safety cannot be proven."));
        }
    }

    private static Entity RequireRollbackEntity(
        Database database,
        Transaction transaction,
        CadCheckpointEntity expected)
    {
        var conflicts = new List<object>();
        InspectRollbackEntity(database, transaction, expected, conflicts);
        if (conflicts.Count != 0 ||
            !TryObjectId(database, expected.Handle, out var objectId) ||
            transaction.GetObject(objectId, OpenMode.ForRead) is not Entity entity)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Rollback entity changed or dependency safety is unproven.");
        }
        return entity;
    }

    private static bool DependencySafe(
        Database database,
        Transaction transaction,
        Entity entity)
    {
        var blocks = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead);
        return entity.OwnerId == blocks[BlockTableRecord.ModelSpace] &&
               entity.GetPersistentReactorIds().Count == 0;
    }

    private static bool TryObjectId(Database database, string handle, out ObjectId objectId)
    {
        try
        {
            objectId = database.GetObjectId(
                false,
                new Handle(long.Parse(handle, NumberStyles.HexNumber, CultureInfo.InvariantCulture)),
                0);
            return !objectId.IsNull && objectId.IsValid && !objectId.IsErased;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception)
        {
            objectId = ObjectId.Null;
            return false;
        }
    }

    private static object Conflict(string code, string? handle, string summary) => new
    {
        code,
        handle,
        summary
    };

    private void AssertCheckpointPins(CadRollbackCheckpointV1 checkpoint)
    {
        CadProgramV02Parser.AssertHostBinding(checkpoint.RuntimeAndPolicyPins, _host);
    }

    private static object RuntimePins(CadRollbackCheckpointV1 checkpoint) => new
    {
        runtime_id = checkpoint.RuntimeAndPolicyPins.RuntimeId,
        runtime_role = checkpoint.RuntimeAndPolicyPins.RuntimeRole,
        host_family = checkpoint.RuntimeAndPolicyPins.HostFamily,
        host_version = checkpoint.RuntimeAndPolicyPins.HostVersion,
        host_package_id = checkpoint.RuntimeAndPolicyPins.PackageId,
        host_package_version = checkpoint.RuntimeAndPolicyPins.PackageVersion,
        host_package_hash = checkpoint.RuntimeAndPolicyPins.PackageHash
    };

    private static object PolicyPins(CadRollbackCheckpointV1 checkpoint) => new
    {
        capability_manifest_hash = checkpoint.RuntimeAndPolicyPins.CapabilityManifestHash,
        operation_registry_hash = checkpoint.RuntimeAndPolicyPins.OperationRegistryHash,
        registry_version = checkpoint.RuntimeAndPolicyPins.OperationRegistryVersion,
        policy_version = checkpoint.RuntimeAndPolicyPins.PolicyVersion
    };

    private static object RollbackCommitResult(
        DurableRollbackReceiptV1 receipt,
        bool duplicate) => new
    {
        rollback_receipt_id = receipt.RollbackReceiptId,
        receipt_digest = receipt.ReceiptDigest,
        original_receipt_id = receipt.OriginalReceiptId,
        checkpoint_id = receipt.CheckpointId,
        checkpoint_digest = receipt.CheckpointDigest,
        rollback_plan_id = receipt.RollbackPlanId,
        rollback_plan_digest = receipt.RollbackPlanDigest,
        rollback_execution_digest = receipt.RollbackExecutionDigest,
        document_revision_before = receipt.DocumentRevisionBefore,
        document_revision_after = receipt.DocumentRevisionAfter,
        removed_entity_count = receipt.RemovedEntities.Count,
        receipt,
        milestone = "effect_and_receipt_committed",
        duplicate
    };

    private static void AssertRollbackDuplicate(
        DurableRollbackReceiptV1 receipt,
        CadRollbackRequest request)
    {
        if (receipt.CheckpointId != request.CheckpointId ||
            receipt.CheckpointDigest != request.CheckpointDigest ||
            receipt.RollbackPlanId != request.RollbackPlanId ||
            receipt.RollbackPlanDigest != request.RollbackPlanDigest ||
            receipt.RollbackExecutionDigest != request.RollbackExecutionDigest)
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "Rollback receipt ID was reused with a conflicting payload.");
        }
    }

    private static object CommitResult(
        DurableProgramReceiptV02 receipt,
        CadRollbackCheckpointV1? checkpoint,
        bool duplicate) => new
    {
        receipt_id = receipt.ReceiptId,
        receipt_digest = receipt.ReceiptDigest,
        document_revision_before = receipt.DocumentRevisionBefore,
        document_revision_after = receipt.DocumentRevisionAfter,
        created_entity_count = receipt.Entities.Count,
        rollback_eligible = checkpoint is not null,
        checkpoint_id = checkpoint?.CheckpointId,
        checkpoint_digest = checkpoint?.CheckpointDigest,
        checkpoint,
        milestone = "effect_and_receipt_committed",
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
        IReadOnlyList<string> EnsuredLayers,
        bool CreatedNonEntityObject);

    private sealed record RollbackPreviewRecord(
        string RollbackPlanId,
        string CheckpointId,
        string CheckpointDigest,
        string RollbackExecutionDigest,
        string ExpiresAt,
        string DocumentRevision,
        bool Eligible);
}

using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace AutocadMcp.Host.R25;

internal sealed record Phase8PreviewResult(
    IReadOnlyList<Phase8PreviewOutput> CreatedOutputs,
    IReadOnlyList<Phase8PreviewModification> ModifiedEntities,
    bool TransactionAborted);

internal sealed record Phase8PreviewOutput(
    string OperationId,
    string OutputId,
    string EntityType,
    string Layer,
    string Fingerprint);

internal sealed record Phase8PreviewModification(
    string OperationId,
    string EntityType,
    string FingerprintBefore,
    string FingerprintAfter);

internal sealed record Phase8CommitResult(
    CadManagedCommitRecord CommitRecord,
    CadCreatedOutputCheckpointV1? CreatedCheckpointV1,
    CadRollbackCheckpointV2? CheckpointV2,
    bool Duplicate);

internal sealed record Phase8RestoreResult(
    CadManagedRestoreReceipt Receipt,
    bool Duplicate);

internal sealed record Phase8CreatedRollbackResult(
    CadCreatedOutputRollbackReceiptV1 Receipt,
    bool Duplicate);

/// <summary>
/// Exact Managed R25 implementation for the Phase 8 create-equivalent and
/// transform packs. This class accepts only compiler-resolved typed operations;
/// it does not parse source programs or invoke commands/reflection.
/// </summary>
internal static class Phase8ManagedOperationPack
{
    public static Phase8PreviewResult Preview(
        Database database,
        CadManagedSealedPlan plan,
        CadManagedHostAdmission admission)
    {
        admission.AssertAllowed(plan);
        using var transaction = database.TransactionManager.StartTransaction();
        var result = Apply(database, transaction, plan);
        transaction.Abort();
        return new Phase8PreviewResult(
            result.CreatedOutputs.Select(output => new Phase8PreviewOutput(
                output.OperationId,
                output.OutputId,
                output.EntityType,
                output.Layer,
                output.Fingerprint)).ToArray(),
            result.ModifiedEntities.Select(entity => new Phase8PreviewModification(
                entity.OperationId,
                entity.EntityType,
                entity.FingerprintBefore,
                entity.FingerprintAfter)).ToArray(),
            TransactionAborted: true);
    }

    public static Phase8CommitResult Commit(
        Database database,
        CadManagedSealedPlan plan,
        CadManagedHostAdmission admission,
        string documentRevisionAfter,
        DateTimeOffset now)
    {
        admission.AssertAllowed(plan);
        var identity = CadManagedEffectIdentity.Create(plan);
        using var transaction = database.TransactionManager.StartTransaction();
        var existing = DrawingProgramLedger.FindPhase8Commit(
            database,
            transaction,
            identity.ReceiptId);
        if (existing is not null)
        {
            AssertDuplicate(existing, plan, identity);
            var duplicateCheckpoint = existing.Receipt.CheckpointV2Id is null
                ? null
                : DrawingProgramLedger.FindCheckpointV2(
                    database,
                    transaction,
                    existing.Receipt.CheckpointV2Id);
            var duplicateCreatedCheckpoint = existing.Receipt.CheckpointV1Id is null
                ? null
                : DrawingProgramLedger.FindPhase8CreatedCheckpoint(
                    database,
                    transaction,
                    existing.Receipt.CheckpointV1Id);
            transaction.Abort();
            return new Phase8CommitResult(
                existing,
                duplicateCreatedCheckpoint,
                duplicateCheckpoint,
                Duplicate: true);
        }

        var applied = Apply(database, transaction, plan);
        var createdCheckpointV1 = applied.CreatedOutputs.Count == 0
            ? null
            : CadCreatedOutputCheckpointV1.Create(
                plan,
                identity,
                documentRevisionAfter,
                applied.CreatedOutputs,
                now);
        CadRollbackCheckpointV2? checkpointV2 = null;
        if (applied.RestoreEntries.Count != 0)
        {
            checkpointV2 = CadRollbackCheckpointV2.Create(
                identity.ReceiptId,
                plan.PlanDigest,
                plan.EffectDigest,
                plan.DocumentId,
                plan.DocumentRevision,
                documentRevisionAfter,
                applied.RestoreEntries,
                plan.Pins,
                plan.OwnerId,
                plan.DeviceId,
                plan.SnapshotId,
                plan.TargetSetDigest,
                now);
        }

        var receipt = new CadManagedOperationReceipt(
            identity.ReceiptId,
            plan.PlanDigest,
            plan.EffectDigest,
            identity.EffectIdentityDigest,
            plan.TargetSetDigest,
            plan.DocumentId,
            plan.DocumentRevision,
            documentRevisionAfter,
            applied.CreatedOutputs,
            applied.ModifiedEntities,
            createdCheckpointV1?.CheckpointId,
            createdCheckpointV1?.CheckpointDigest,
            checkpointV2?.CheckpointId,
            checkpointV2?.CheckpointDigest,
            plan.Pins);
        var commitRecord = CadManagedCommitRecord.Create(receipt, now);

        DrawingProgramLedger.AddPhase8Commit(database, transaction, commitRecord);
        if (createdCheckpointV1 is not null)
        {
            DrawingProgramLedger.AddPhase8CreatedCheckpoint(
                database,
                transaction,
                createdCheckpointV1);
        }
        if (checkpointV2 is not null)
        {
            DrawingProgramLedger.AddCheckpointV2(database, transaction, checkpointV2);
        }
        transaction.Commit();
        return new Phase8CommitResult(
            commitRecord,
            createdCheckpointV1,
            checkpointV2,
            Duplicate: false);
    }

    public static IReadOnlyList<string> Validate(
        Database database,
        Transaction transaction,
        CadManagedCommitRecord commitRecord)
    {
        var failures = new List<string>();
        foreach (var output in commitRecord.Receipt.CreatedOutputs)
        {
            if (!TryEntity(database, transaction, output.EntityId, out var entity) ||
                entity.GetRXClass().DxfName != output.EntityType ||
                !string.Equals(entity.Layer, output.Layer, StringComparison.OrdinalIgnoreCase) ||
                Fingerprint(entity, transaction) != output.Fingerprint)
            {
                failures.Add($"created_output_changed:{output.OutputId}");
            }
        }
        foreach (var modified in commitRecord.Receipt.ModifiedEntities)
        {
            if (!TryEntity(database, transaction, modified.EntityId, out var entity) ||
                entity.GetRXClass().DxfName != modified.EntityType ||
                Fingerprint(entity, transaction) != modified.FingerprintAfter)
            {
                failures.Add($"transform_result_changed:{modified.OperationId}");
            }
        }
        return failures;
    }

    public static Phase8RestoreResult Restore(
        Database database,
        CadRollbackCheckpointV2 checkpoint,
        string gatewayPinnedCheckpointDigest,
        string currentDocumentRevision,
        string documentRevisionAfterRestore,
        CadManagedHostAdmission admission,
        DateTimeOffset now)
    {
        if (!admission.CheckpointV2Enabled ||
            !admission.TransformPackEnabled)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Transform restore remains disabled.");
        }
        if (checkpoint.CheckpointDigest != gatewayPinnedCheckpointDigest ||
            checkpoint.RuntimePins != admission.ActualPins)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Checkpoint provenance differs from independently pinned evidence.");
        }
        using var transaction = database.TransactionManager.StartTransaction();
        var existing = DrawingProgramLedger.FindPhase8RestoreReceipt(
            database,
            transaction,
            CadManagedRestoreReceipt.BuildReceiptId(checkpoint));
        if (existing is not null)
        {
            if (existing.RestoreIdentityDigest !=
                    CadManagedRestoreReceipt.BuildIdentityDigest(checkpoint) ||
                existing.CheckpointDigest != gatewayPinnedCheckpointDigest ||
                existing.Pins != admission.ActualPins)
            {
                throw new ProtocolValidationException(
                    "duplicate_payload_mismatch",
                    "Restore receipt identity conflicts with the checkpoint.");
            }
            transaction.Abort();
            return new Phase8RestoreResult(existing, Duplicate: true);
        }
        if (checkpoint.DocumentRevisionAfter != currentDocumentRevision)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Strict document revision changed after the transform commit.");
        }
        var restored = new List<CadManagedModifiedEntity>();
        foreach (var restore in checkpoint.RestoreEntries)
        {
            var entity = RequireEntity(
                database,
                transaction,
                restore.TargetBefore,
                expectedFingerprint: restore.FingerprintAfter);
            AssertDependencies(
                transaction,
                entity,
                restore.Dependencies,
                restore.DependencyClosureDigest);
            entity.UpgradeOpen();
            RestoreDescriptor(entity, restore.RestoreDescriptor);
            var restoredFingerprint = Fingerprint(entity, transaction);
            if (restoredFingerprint != restore.TargetBefore.Fingerprint)
            {
                throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Restored entity does not match its Host-generated pre-image.");
            }
            restored.Add(new CadManagedModifiedEntity(
                restore.OperationId,
                entity.Handle.ToString(),
                entity.GetRXClass().DxfName,
                restore.FingerprintAfter,
                restoredFingerprint));
        }
        var receipt = CadManagedRestoreReceipt.Create(
            checkpoint,
            documentRevisionAfterRestore,
            restored,
            now);
        DrawingProgramLedger.AddPhase8RestoreReceipt(
            database,
            transaction,
            receipt);
        transaction.Commit();
        return new Phase8RestoreResult(receipt, Duplicate: false);
    }

    public static Phase8CreatedRollbackResult RollbackCreatedOutputs(
        Database database,
        CadCreatedOutputCheckpointV1 checkpoint,
        string gatewayPinnedCheckpointDigest,
        string currentDocumentRevision,
        string documentRevisionAfterRollback,
        CadManagedHostAdmission admission,
        DateTimeOffset now)
    {
        if (!admission.CreatePackEnabled)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Managed create-equivalent rollback is disabled.");
        }
        if (checkpoint.CheckpointDigest != gatewayPinnedCheckpointDigest ||
            checkpoint.Pins != admission.ActualPins)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Created-output checkpoint differs from pinned evidence.");
        }
        using var transaction = database.TransactionManager.StartTransaction();
        var receiptId = CadCreatedOutputRollbackReceiptV1.BuildReceiptId(checkpoint);
        var existing = DrawingProgramLedger.FindPhase8CreatedRollbackReceipt(
            database,
            transaction,
            receiptId);
        if (existing is not null)
        {
            if (existing.RollbackIdentityDigest !=
                    CadCreatedOutputRollbackReceiptV1.BuildIdentityDigest(checkpoint) ||
                existing.CheckpointDigest != gatewayPinnedCheckpointDigest ||
                existing.Pins != admission.ActualPins)
            {
                throw new ProtocolValidationException(
                    "duplicate_payload_mismatch",
                    "Created-output rollback receipt identity conflicts.");
            }
            transaction.Abort();
            return new Phase8CreatedRollbackResult(existing, Duplicate: true);
        }
        if (checkpoint.DocumentRevisionAfter != currentDocumentRevision)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Strict document revision changed after create-equivalent commit.");
        }

        var blockTable = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead);
        var modelSpaceId = blockTable[BlockTableRecord.ModelSpace];
        var removed = new List<CadRemovedOutputV1>();
        foreach (var output in checkpoint.CreatedOutputs)
        {
            if (!TryEntity(database, transaction, output.EntityId, out var entity) ||
                entity.GetRXClass().DxfName != output.EntityType ||
                !string.Equals(entity.Layer, output.Layer, StringComparison.OrdinalIgnoreCase) ||
                Fingerprint(entity, transaction) != output.Fingerprint)
            {
                throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Created output changed before rollback.");
            }
            RequireModelSpaceAndNoReactors(entity, modelSpaceId);
            removed.Add(new CadRemovedOutputV1(
                output.OutputId,
                output.EntityId,
                output.EntityType,
                output.Fingerprint));
            entity.UpgradeOpen();
            entity.Erase();
        }
        var receipt = CadCreatedOutputRollbackReceiptV1.Create(
            checkpoint,
            documentRevisionAfterRollback,
            removed,
            now);
        DrawingProgramLedger.AddPhase8CreatedRollbackReceipt(
            database,
            transaction,
            receipt);
        transaction.Commit();
        return new Phase8CreatedRollbackResult(receipt, Duplicate: false);
    }

    private static Phase8ApplyResult Apply(
        Database database,
        Transaction transaction,
        CadManagedSealedPlan plan)
    {
        var blockTable = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead);
        var modelSpace = (BlockTableRecord)transaction.GetObject(
            blockTable[BlockTableRecord.ModelSpace],
            OpenMode.ForWrite);
        var outputs = new List<CadManagedOutputMapping>();
        var modified = new List<CadManagedModifiedEntity>();
        var restoreEntries = new List<CadRestoreEntryV2>();

        foreach (var operation in plan.Operations)
        {
            if (operation.Target.DocumentId != plan.DocumentId)
            {
                throw new ProtocolValidationException(
                    "document_changed",
                    "Operation target belongs to another drawing.");
            }
            var source = RequireEntity(
                database,
                transaction,
                operation.Target,
                operation.Target.Fingerprint);
            RequireModelSpaceAndNoReactors(source, modelSpace.ObjectId);
            var before = Fingerprint(source, transaction);

            switch (operation)
            {
                case CadCopyEntityOperation copy:
                    AddClone(
                        modelSpace,
                        transaction,
                        source,
                        Matrix3d.Displacement(ToVector(copy.Displacement)),
                        copy,
                        copy.OutputId,
                        outputs);
                    break;
                case CadLinearPatternOperation linear:
                    for (var index = 1; index < linear.Count; index++)
                    {
                        AddClone(
                            modelSpace,
                            transaction,
                            source,
                            Matrix3d.Displacement(ToVector(linear.Step).MultiplyBy(index)),
                            linear,
                            linear.StableOutputIds[index - 1],
                            outputs);
                    }
                    break;
                case CadRectangularPatternOperation rectangular:
                    var outputIndex = 0;
                    for (var row = 0; row < rectangular.RowCount; row++)
                    {
                        for (var column = 0; column < rectangular.ColumnCount; column++)
                        {
                            if (row == 0 && column == 0)
                            {
                                continue;
                            }
                            var displacement =
                                ToVector(rectangular.ColumnStep).MultiplyBy(column) +
                                ToVector(rectangular.RowStep).MultiplyBy(row);
                            AddClone(
                                modelSpace,
                                transaction,
                                source,
                                Matrix3d.Displacement(displacement),
                                rectangular,
                                rectangular.StableOutputIds[outputIndex++],
                                outputs);
                        }
                    }
                    break;
                case CadPolarPatternOperation polar:
                    var angleStep = polar.TotalAngleRadians / polar.Count;
                    for (var index = 1; index < polar.Count; index++)
                    {
                        AddClone(
                            modelSpace,
                            transaction,
                            source,
                            Matrix3d.Rotation(
                                angleStep * index,
                                Vector3d.ZAxis,
                                ToPoint(polar.Center)),
                            polar,
                            polar.StableOutputIds[index - 1],
                            outputs);
                    }
                    break;
                case CadOffsetEntityOperation offset:
                    AddOffset(
                        modelSpace,
                        transaction,
                        source,
                        offset,
                        outputs);
                    break;
                case CadMoveEntityOperation move:
                    AddRestoreEntry(
                        source,
                        transaction,
                        move,
                        plan.SnapshotId,
                        restoreEntries);
                    source.UpgradeOpen();
                    source.TransformBy(
                        Matrix3d.Displacement(ToVector(move.Displacement)));
                    AddModified(source, transaction, move, before, modified, restoreEntries);
                    break;
                case CadRotateEntityOperation rotate:
                    AddRestoreEntry(
                        source,
                        transaction,
                        rotate,
                        plan.SnapshotId,
                        restoreEntries);
                    source.UpgradeOpen();
                    source.TransformBy(Matrix3d.Rotation(
                        rotate.AngleRadians,
                        Vector3d.ZAxis,
                        ToPoint(rotate.BasePoint)));
                    AddModified(source, transaction, rotate, before, modified, restoreEntries);
                    break;
                case CadScaleEntityOperation scale:
                    AddRestoreEntry(
                        source,
                        transaction,
                        scale,
                        plan.SnapshotId,
                        restoreEntries);
                    source.UpgradeOpen();
                    source.TransformBy(Matrix3d.Scaling(
                        scale.UniformFactor,
                        ToPoint(scale.BasePoint)));
                    AddModified(source, transaction, scale, before, modified, restoreEntries);
                    break;
                default:
                    throw new ProtocolValidationException(
                        "capability_missing",
                        "Operation is outside the explicit Managed R25 registry.");
            }

            if (operation is CadCopyEntityOperation or
                CadLinearPatternOperation or
                CadRectangularPatternOperation or
                CadPolarPatternOperation or
                CadOffsetEntityOperation)
            {
                if (Fingerprint(source, transaction) != before)
                {
                    throw new ProtocolValidationException(
                        "commit_validation_failed",
                        "Create-equivalent operation changed its source entity.");
                }
            }
        }
        return new Phase8ApplyResult(outputs, modified, restoreEntries);
    }

    private static void AddClone(
        BlockTableRecord modelSpace,
        Transaction transaction,
        Entity source,
        Matrix3d transform,
        CadManagedOperation operation,
        string outputId,
        List<CadManagedOutputMapping> outputs)
    {
        if (source.Clone() is not Entity clone)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Entity cannot be cloned by the exact Managed R25 pack.");
        }
        clone.TransformBy(transform);
        AddCreated(modelSpace, transaction, clone, operation, outputId, outputs);
    }

    private static void AddOffset(
        BlockTableRecord modelSpace,
        Transaction transaction,
        Entity source,
        CadOffsetEntityOperation operation,
        List<CadManagedOutputMapping> outputs)
    {
        if (source is not Curve curve)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Offset requires an allowlisted curve entity.");
        }
        var candidates = curve.GetOffsetCurves(operation.SignedDistance);
        if (candidates.Count != 1 ||
            candidates[0] is not Entity offset ||
            offset.GetRXClass().DxfName != source.GetRXClass().DxfName)
        {
            foreach (DBObject candidate in candidates)
            {
                candidate.Dispose();
            }
            throw new ProtocolValidationException(
                "capability_missing",
                "Offset result is ambiguous or outside the allowlisted entity type.");
        }
        AddCreated(
            modelSpace,
            transaction,
            offset,
            operation,
            operation.OutputId,
            outputs);
    }

    private static void AddCreated(
        BlockTableRecord modelSpace,
        Transaction transaction,
        Entity entity,
        CadManagedOperation operation,
        string outputId,
        List<CadManagedOutputMapping> outputs)
    {
        modelSpace.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
        outputs.Add(new CadManagedOutputMapping(
            operation.OperationId,
            outputId,
            entity.Handle.ToString(),
            entity.GetRXClass().DxfName,
            entity.Layer,
            Fingerprint(entity, transaction)));
    }

    private static void AddRestoreEntry(
        Entity entity,
        Transaction transaction,
        CadManagedOperation operation,
        string snapshotId,
        List<CadRestoreEntryV2> entries)
    {
        var dependencies = CaptureDependencies(entity, transaction);
        var descriptor = CaptureDescriptor(entity, transaction);
        entries.Add(new CadRestoreEntryV2(
            operation.OperationId,
            operation.Target,
            $"sha256:{new string('0', 64)}",
            "MODEL_SPACE",
            snapshotId,
            dependencies,
            CadRestoreEvidenceDigest.Dependencies(dependencies),
            descriptor,
            CadRestoreEvidenceDigest.Descriptor(descriptor)));
    }

    private static void AddModified(
        Entity entity,
        Transaction transaction,
        CadManagedOperation operation,
        string fingerprintBefore,
        List<CadManagedModifiedEntity> modified,
        List<CadRestoreEntryV2> restoreEntries)
    {
        var after = Fingerprint(entity, transaction);
        var entryIndex = restoreEntries.FindIndex(
            item => item.OperationId == operation.OperationId);
        restoreEntries[entryIndex] = restoreEntries[entryIndex] with
        {
            FingerprintAfter = after
        };
        modified.Add(new CadManagedModifiedEntity(
            operation.OperationId,
            entity.Handle.ToString(),
            entity.GetRXClass().DxfName,
            fingerprintBefore,
            after));
    }

    private static CadEntityRestoreDescriptorV2 CaptureDescriptor(
        Entity entity,
        Transaction transaction)
    {
        var style = new CadEntityStyleV2(
            entity.Layer,
            entity.Linetype,
            entity.LinetypeScale,
            (int)entity.LineWeight,
            entity.Visible,
            checked((short)entity.ColorIndex));
        return entity switch
        {
            Line line => new CadEntityRestoreDescriptorV2(
                "cad.restore-descriptor/2",
                "restore_allowlisted_preimage",
                "LINE",
                style,
                FromPoint(line.StartPoint),
                FromPoint(line.EndPoint),
                null,
                null,
                null,
                null,
                null,
                null),
            Circle circle when IsParallelToZ(circle.Normal) =>
                new CadEntityRestoreDescriptorV2(
                    "cad.restore-descriptor/2",
                    "restore_allowlisted_preimage",
                    "CIRCLE",
                    style,
                    null,
                    null,
                    FromPoint(circle.Center),
                    FromVector(circle.Normal),
                    circle.Radius,
                    null,
                    null,
                    null),
            Polyline polyline => new CadEntityRestoreDescriptorV2(
                "cad.restore-descriptor/2",
                "restore_allowlisted_preimage",
                "LWPOLYLINE",
                style,
                null,
                null,
                null,
                null,
                null,
                polyline.Elevation,
                polyline.Closed,
                Enumerable.Range(0, polyline.NumberOfVertices)
                    .Select(index =>
                    {
                        var point = polyline.GetPoint2dAt(index);
                        return new CadLwPolylineVertexV2(
                            point.X,
                            point.Y,
                            polyline.GetBulgeAt(index));
                    }).ToArray()),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "Entity has no bounded checkpoint v2 restore descriptor.")
        };
    }

    private static IReadOnlyList<CadDependencyRefV2> CaptureDependencies(
        Entity entity,
        Transaction transaction)
    {
        var layer = (LayerTableRecord)transaction.GetObject(
            entity.LayerId,
            OpenMode.ForRead);
        var linetype = (LinetypeTableRecord)transaction.GetObject(
            entity.LinetypeId,
            OpenMode.ForRead);
        return
        [
            new CadDependencyRefV2(
                "layer",
                layer.Name,
                DependencyFingerprint(layer)),
            new CadDependencyRefV2(
                "linetype",
                linetype.Name,
                DependencyFingerprint(linetype))
        ];
    }

    private static void AssertDependencies(
        Transaction transaction,
        Entity entity,
        IReadOnlyList<CadDependencyRefV2> expected,
        string expectedClosureDigest)
    {
        var actual = CaptureDependencies(entity, transaction);
        if (expectedClosureDigest != CadRestoreEvidenceDigest.Dependencies(expected) ||
            actual.Count != expected.Count ||
            actual.Where((dependency, index) => dependency != expected[index]).Any())
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Restore dependency closure changed.");
        }
    }

    private static string DependencyFingerprint(SymbolTableRecord dependency)
    {
        JsonObject properties = dependency switch
        {
            LayerTableRecord layer => new JsonObject
            {
                ["name"] = layer.Name,
                ["color_index"] = layer.Color.ColorIndex,
                ["is_frozen"] = layer.IsFrozen,
                ["is_locked"] = layer.IsLocked,
                ["is_off"] = layer.IsOff,
                ["is_plottable"] = layer.IsPlottable
            },
            LinetypeTableRecord linetype => new JsonObject
            {
                ["name"] = linetype.Name,
                ["comments"] = linetype.Comments,
                ["pattern_length"] = linetype.PatternLength,
                ["dashes"] = new JsonArray(
                    Enumerable.Range(0, linetype.NumDashes)
                        .Select(index => (JsonNode)linetype.DashLengthAt(index))
                        .ToArray())
            },
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "Entity dependency type is unsupported.")
        };
        var value = new JsonObject
        {
            ["entity_type"] = dependency.GetRXClass().DxfName,
            ["properties"] = properties
        };
        using var document = JsonDocument.Parse(value.ToJsonString());
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static void RestoreDescriptor(
        Entity entity,
        CadEntityRestoreDescriptorV2 descriptor)
    {
        descriptor.Validate();
        if (entity.GetRXClass().DxfName != descriptor.EntityType)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Restore target type changed.");
        }
        entity.Layer = descriptor.Style.Layer;
        entity.Linetype = descriptor.Style.Linetype;
        entity.LinetypeScale = descriptor.Style.LinetypeScale;
        entity.LineWeight = (LineWeight)descriptor.Style.LineWeight;
        entity.Visible = descriptor.Style.Visible;
        entity.ColorIndex = descriptor.Style.ColorIndex;

        switch (entity)
        {
            case Line line:
                line.StartPoint = ToPoint(descriptor.LineStart!);
                line.EndPoint = ToPoint(descriptor.LineEnd!);
                break;
            case Circle circle:
                circle.Center = ToPoint(descriptor.CircleCenter!);
                circle.Normal = ToVector(descriptor.CircleNormal!);
                circle.Radius = descriptor.CircleRadius!.Value;
                break;
            case Polyline polyline:
                var vertices = descriptor.PolylineVertices!;
                while (polyline.NumberOfVertices > vertices.Count)
                {
                    polyline.RemoveVertexAt(polyline.NumberOfVertices - 1);
                }
                for (var index = 0; index < vertices.Count; index++)
                {
                    var vertex = vertices[index];
                    var point = new Point2d(vertex.X, vertex.Y);
                    if (index < polyline.NumberOfVertices)
                    {
                        polyline.SetPointAt(index, point);
                        polyline.SetBulgeAt(index, vertex.Bulge);
                    }
                    else
                    {
                        polyline.AddVertexAt(index, point, vertex.Bulge, 0, 0);
                    }
                }
                polyline.Elevation = descriptor.PolylineElevation!.Value;
                polyline.Closed = descriptor.PolylineClosed!.Value;
                break;
            default:
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Restore target type is outside the explicit allowlist.");
        }
    }

    private static Entity RequireEntity(
        Database database,
        Transaction transaction,
        CadStableEntityRef target,
        string expectedFingerprint)
    {
        target.Validate();
        if (!TryEntity(database, transaction, target.EntityId, out var entity) ||
            entity.GetRXClass().DxfName != target.EntityType)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Stable target is missing or has an unsupported type.");
        }
        if (Fingerprint(entity, transaction) != expectedFingerprint)
        {
            throw new ProtocolValidationException(
                "document_changed",
                "Stable target fingerprint changed.");
        }
        return entity;
    }

    private static bool TryEntity(
        Database database,
        Transaction transaction,
        string entityId,
        out Entity entity)
    {
        try
        {
            var objectId = database.GetObjectId(
                false,
                new Handle(long.Parse(
                    entityId,
                    NumberStyles.HexNumber,
                    CultureInfo.InvariantCulture)),
                0);
            entity = (Entity)transaction.GetObject(objectId, OpenMode.ForRead);
            return !objectId.IsNull &&
                   objectId.IsValid &&
                   !objectId.IsErased;
        }
        catch (Exception exception) when (
            exception is Autodesk.AutoCAD.Runtime.Exception or
            FormatException or
            InvalidCastException or
            OverflowException)
        {
            entity = null!;
            return false;
        }
    }

    private static void RequireModelSpaceAndNoReactors(
        Entity entity,
        ObjectId modelSpaceId)
    {
        if (entity.OwnerId != modelSpaceId ||
            entity.GetPersistentReactorIds().Count != 0)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Only dependency-free model-space entities are supported.");
        }
    }

    private static string Fingerprint(Entity entity, Transaction transaction)
    {
        var geometry = new JsonObject();
        switch (entity)
        {
            case Line line:
                geometry["start"] = PointJson(line.StartPoint);
                geometry["end"] = PointJson(line.EndPoint);
                break;
            case Circle circle when IsParallelToZ(circle.Normal):
                geometry["center"] = PointJson(circle.Center);
                geometry["normal"] = VectorJson(circle.Normal);
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
            default:
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Entity type has no exact Phase 8 fingerprint.");
        }
        var extents = entity.GeometricExtents;
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
                ["min"] = PointJson(extents.MinPoint),
                ["max"] = PointJson(extents.MaxPoint)
            },
            ["geometry"] = geometry
        };
        using var document = JsonDocument.Parse(value.ToJsonString());
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static void AssertDuplicate(
        CadManagedCommitRecord existing,
        CadManagedSealedPlan plan,
        CadManagedEffectIdentity identity)
    {
        if (existing.Receipt.ReceiptId != identity.ReceiptId ||
            existing.Receipt.EffectIdentityDigest != identity.EffectIdentityDigest ||
            existing.Receipt.PlanDigest != plan.PlanDigest ||
            existing.Receipt.EffectDigest != plan.EffectDigest ||
            existing.Receipt.TargetSetDigest != plan.TargetSetDigest ||
            existing.Receipt.DocumentId != plan.DocumentId ||
            existing.Receipt.Pins != plan.Pins)
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "Phase 8 receipt ID was reused with another sealed plan.");
        }
    }

    private static bool IsParallelToZ(Vector3d normal) =>
        normal.IsParallelTo(Vector3d.ZAxis, new Tolerance(1e-10, 1e-10));

    private static Point3d ToPoint(CadManagedPoint point) =>
        new(point.X, point.Y, point.Z);

    private static Vector3d ToVector(CadManagedVector vector) =>
        new(vector.X, vector.Y, vector.Z);

    private static CadManagedPoint FromPoint(Point3d point) =>
        new(point.X, point.Y, point.Z);

    private static CadManagedVector FromVector(Vector3d vector) =>
        new(vector.X, vector.Y, vector.Z);

    private static JsonObject PointJson(Point3d point) => new()
    {
        ["x"] = point.X,
        ["y"] = point.Y,
        ["z"] = point.Z
    };

    private static JsonObject VectorJson(Vector3d vector) => new()
    {
        ["x"] = vector.X,
        ["y"] = vector.Y,
        ["z"] = vector.Z
    };

    private sealed record Phase8ApplyResult(
        IReadOnlyList<CadManagedOutputMapping> CreatedOutputs,
        IReadOnlyList<CadManagedModifiedEntity> ModifiedEntities,
        IReadOnlyList<CadRestoreEntryV2> RestoreEntries);
}

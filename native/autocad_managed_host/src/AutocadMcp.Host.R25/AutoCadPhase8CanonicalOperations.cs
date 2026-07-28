using System.Globalization;
using System.Text.Json;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using Document = Autodesk.AutoCAD.ApplicationServices.Document;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadPhase8CanonicalOperations(
    AutoCadIdleScheduler scheduler,
    DocumentIdentityRegistry identities,
    string packageHash)
{
    private readonly bool _sourceEnabled =
        Environment.GetEnvironmentVariable(
            "AUTOCAD_MCP_PROGRAM_V1_SOURCE_ENABLED") == "1";
    private readonly bool _createPackEnabled =
        Environment.GetEnvironmentVariable(
            "AUTOCAD_MCP_PROGRAM_V1_CREATE_PACK_ENABLED") == "1";
    private readonly bool _transformPackEnabled =
        Environment.GetEnvironmentVariable(
            "AUTOCAD_MCP_PROGRAM_V1_TRANSFORM_PACK_ENABLED") == "1";
    private readonly bool _checkpointV2Enabled =
        Environment.GetEnvironmentVariable(
            "AUTOCAD_MCP_CHECKPOINT_V2_ENABLED") == "1";
    private readonly Phase8HostRuntimeEvidence _runtime =
        Phase8HostRuntimeEvidence.Create(
            HostConstants.HostVersion,
            HostConstants.PackageId,
            HostConstants.PackageVersion,
            packageHash);
    private readonly Dictionary<string, CanonicalRollbackPlan> _rollbackPlans =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, ManagedRollbackPlan> _managedRollbackPlans =
        new(StringComparer.Ordinal);

    public Phase8HostRuntimeEvidence RuntimeEvidence => _runtime;
    public bool SourceEnabled => _sourceEnabled;
    public bool CreatePackEnabled => _createPackEnabled;
    public bool TransformPackEnabled => _transformPackEnabled;
    public bool CheckpointV2Enabled => _checkpointV2Enabled;
    public bool Enabled => _sourceEnabled &&
        (_createPackEnabled || _transformPackEnabled);

    public static bool IsCanonicalRecovery(CommandRequest request)
    {
        if (!Phase7RollbackContract.OperationIds.Contains(request.OperationId))
        {
            return false;
        }
        var property = request.OperationId switch
        {
            "cad.recovery.receipt_query" => "receipt_id",
            "cad.rollback.checkpoint.lookup" or
            "cad.rollback.preview" or
            "cad.rollback.commit" => "checkpoint_id",
            "cad.rollback.validate" => "rollback_receipt_id",
            _ => ""
        };
        if (property.Length == 0 ||
            !request.Arguments.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.String)
        {
            return false;
        }
        var identifier = value.GetString() ?? "";
        return identifier.StartsWith("AUTOCAD_MCP_PHASE8_", StringComparison.Ordinal) ||
            identifier.StartsWith("AUTOCAD_MCP_CHECKPOINT_", StringComparison.Ordinal) ||
            identifier.StartsWith("AUTOCAD_MCP_RESTORE_V2_", StringComparison.Ordinal) ||
            identifier.StartsWith("AUTOCAD_MCP_CREATED_RB_", StringComparison.Ordinal);
    }

    public Task<object> ExecuteAsync(
        CommandRequest request,
        CancellationToken cancellationToken) =>
        scheduler.RunAsync<object>(() => Execute(request), cancellationToken);

    private object Execute(CommandRequest request)
    {
        if (!Enabled)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Canonical Phase 8 create path is disabled.");
        }
        CadHostAdmission.AssertCommandState(GetCommandActive());
        CadHostAdmission.AssertDeadline(
            request.DeadlineAt,
            DateTimeOffset.UtcNow,
            CadProgramV02Contract.MaxDeadlineSeconds);
        var document = Application.DocumentManager.MdiActiveDocument
            ?? throw new ProtocolValidationException(
                "no_active_document",
                "No active drawing is open.");
        var identity = identities.Get(document);
        if (identity.DatabaseFingerprint == "unavailable")
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Canonical Phase 8 write requires a stable DWG fingerprint.");
        }
        if (Phase7RollbackContract.OperationIds.Contains(request.OperationId))
        {
            CadHostAdmission.AssertDocument(
                request.DocumentId,
                request.DocumentId ?? string.Empty,
                identity.DocumentId,
                null);
            return ExecuteRecovery(document, identity, request);
        }
        var command = Phase8CanonicalHostCommandParser.Parse(
            request.OperationId,
            request.Arguments,
            _runtime,
            DateTimeOffset.UtcNow);
        if (command.Approval is not null &&
            command.Approval.CommandId != request.CommandId)
        {
            throw new ProtocolValidationException(
                "approval_binding_mismatch",
                "Trusted approval command ID differs from Host envelope.");
        }
        CadHostAdmission.AssertDocument(
            request.DocumentId,
            command.DocumentId,
            identity.DocumentId,
            command.DocumentId);
        AssertRevision(command.ExpectedDocumentRevision, Revision(identity));
        return request.OperationId == "cad.program.preview"
            ? Preview(document, identity, command)
            : Commit(document, identity, command);
    }

    private object ExecuteRecovery(
        Document document,
        DocumentIdentity identity,
        CommandRequest request)
    {
        var command = Phase7RollbackParser.Parse(
            request.OperationId,
            request.Arguments);
        return request.OperationId switch
        {
            "cad.recovery.receipt_query" =>
                QueryReceipt(document, command.ReceiptId!),
            "cad.rollback.checkpoint.lookup" =>
                QueryCheckpoint(document, command.CheckpointId!),
            "cad.rollback.preview" =>
                PreviewRollback(document, identity, command),
            "cad.rollback.commit" =>
                CommitRollback(document, identity, command),
            "cad.rollback.validate" =>
                ValidateRollback(document, command.RollbackReceiptId!),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "Canonical Phase 8 recovery operation is unavailable.")
        };
    }

    private object Preview(
        Document document,
        DocumentIdentity identity,
        Phase8CanonicalHostCommand command)
    {
        var revision = Revision(identity);
        if (command.HasTargetOperations)
        {
            return PreviewManaged(document, identity, command, revision);
        }
        var result = PreviewCore(document, identity, command);
        if (Revision(identity) != revision)
        {
            throw new ProtocolValidationException(
                "preview_abort_failed",
                "Canonical Phase 8 preview changed the drawing.");
        }
        return CommonResult(
            command,
            new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["preview_digest"] = result.Digest,
                ["planned_entities"] = result.Entities,
                ["transaction_aborted"] = true,
                ["drawing_unchanged"] = true
            });
    }

    private object Commit(
        Document document,
        DocumentIdentity identity,
        Phase8CanonicalHostCommand command)
    {
        if (command.HasTargetOperations)
        {
            return CommitManaged(document, identity, command);
        }
        var approval = command.Approval!;
        var preview = PreviewCore(document, identity, command);
        if (preview.Digest != approval.PreviewDigest)
        {
            throw new ProtocolValidationException(
                "preview_mismatch",
                "Commit does not match exact Gateway-approved preview evidence.");
        }
        using (document.LockDocument())
        using (var lookup =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var existing = DrawingProgramLedger.FindPhase8CanonicalReceipt(
                document.Database,
                lookup,
                approval.ReceiptId);
            if (existing is not null)
            {
                AssertDuplicate(existing, command);
                return CommitResult(command, existing, duplicate: true);
            }
        }

        var revisionBefore = Revision(identity);
        AssertRevision(command.ExpectedDocumentRevision, revisionBefore);
        var revisionAfter = IncrementRevision(revisionBefore);
        Phase8CanonicalCreateReceipt receipt;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartTransaction())
        {
            var existing = DrawingProgramLedger.FindPhase8CanonicalReceipt(
                document.Database,
                transaction,
                approval.ReceiptId);
            if (existing is not null)
            {
                AssertDuplicate(existing, command);
                return CommitResult(command, existing, duplicate: true);
            }
            var applied = AutoCadProgramOperations.Apply(
                document.Database,
                transaction,
                command.ToCreateProgram());
            var entities = CreatedEntities(
                command,
                applied.Entities,
                transaction);
            var checkpoint = Phase8CanonicalCreatedCheckpoint.Create(
                command,
                revisionAfter,
                entities,
                DateTimeOffset.UtcNow);
            receipt = Phase8CanonicalCreateReceipt.Create(
                command,
                revisionAfter,
                entities,
                checkpoint.CheckpointId,
                checkpoint.CheckpointDigest,
                DateTimeOffset.UtcNow);
            DrawingProgramLedger.AddPhase8CanonicalCheckpoint(
                document.Database,
                transaction,
                checkpoint);
            DrawingProgramLedger.AddPhase8CanonicalReceipt(
                document.Database,
                transaction,
                receipt);
            transaction.Commit();
        }
        identity.Revision.Record(
            DocumentEventKind.ObjectModified,
            DateTimeOffset.UtcNow,
            receipt.ReceiptId,
            changesContent: true);
        if (Revision(identity) != revisionAfter)
        {
            throw new ProtocolValidationException(
                "commit_validation_failed",
                "Canonical Phase 8 receipt revision differs from drawing.");
        }
        return CommitResult(command, receipt, duplicate: false);
    }

    private object PreviewManaged(
        Document document,
        DocumentIdentity identity,
        Phase8CanonicalHostCommand command,
        string revision)
    {
        var plan = command.ToManagedPlan();
        var admission = ManagedAdmission(command, plan);
        Phase8PreviewResult result;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        {
            result = Phase8ManagedOperationPack.Preview(
                document.Database,
                plan,
                admission);
        }
        if (!result.TransactionAborted || Revision(identity) != revision)
        {
            throw new ProtocolValidationException(
                "preview_abort_failed",
                "Canonical Phase 8 target preview changed the drawing.");
        }
        var evidence = JsonSerializer.SerializeToElement(
            new
            {
                created_outputs = result.CreatedOutputs,
                modified_entities = result.ModifiedEntities
            },
            HostProtocol.JsonOptions);
        return CommonResult(
            command,
            new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["preview_digest"] = command.PreviewEvidenceDigest(evidence),
                ["created_outputs"] = result.CreatedOutputs,
                ["modified_entities"] = result.ModifiedEntities,
                ["transaction_aborted"] = true,
                ["drawing_unchanged"] = true
            });
    }

    private object CommitManaged(
        Document document,
        DocumentIdentity identity,
        Phase8CanonicalHostCommand command)
    {
        var approval = command.Approval!;
        var plan = command.ToManagedPlan();
        var admission = ManagedAdmission(command, plan);
        Phase8PreviewResult preview;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        {
            preview = Phase8ManagedOperationPack.Preview(
                document.Database,
                plan,
                admission);
        }
        var evidence = JsonSerializer.SerializeToElement(
            new
            {
                created_outputs = preview.CreatedOutputs,
                modified_entities = preview.ModifiedEntities
            },
            HostProtocol.JsonOptions);
        if (command.PreviewEvidenceDigest(evidence) != approval.PreviewDigest)
        {
            throw new ProtocolValidationException(
                "preview_mismatch",
                "Commit does not match exact Gateway-approved target preview.");
        }

        var revisionBefore = Revision(identity);
        AssertRevision(command.ExpectedDocumentRevision, revisionBefore);
        var revisionAfter = IncrementRevision(revisionBefore);
        Phase8CommitResult result;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        {
            try
            {
                result = Phase8ManagedOperationPack.Commit(
                    document.Database,
                    plan,
                    admission,
                    revisionAfter,
                    DateTimeOffset.UtcNow,
                    new CadManagedEffectIdentity(
                        approval.ReceiptId,
                        command.EffectIdentityDigest()));
            }
            catch (ProtocolValidationException)
            {
                throw;
            }
            catch (Exception error)
            {
                throw new ProtocolValidationException(
                    "commit_validation_failed",
                    $"Managed Phase 8 commit failed safely ({error.GetType().Name}).");
            }
        }
        if (!result.Duplicate)
        {
            identity.Revision.Record(
                DocumentEventKind.ObjectModified,
                DateTimeOffset.UtcNow,
                result.CommitRecord.Receipt.ReceiptId,
                changesContent: true);
        }
        if (!result.Duplicate && Revision(identity) != revisionAfter)
        {
            throw new ProtocolValidationException(
                "commit_validation_failed",
                "Canonical Phase 8 target receipt revision differs from drawing.");
        }
        var receipt = result.CommitRecord.Receipt;
        return CommonResult(
            command,
            new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["receipt_id"] = receipt.ReceiptId,
                ["receipt_digest"] = result.CommitRecord.ReceiptDigest,
                ["effect_identity_digest"] = receipt.EffectIdentityDigest,
                ["document_revision_before"] = receipt.DocumentRevisionBefore,
                ["document_revision_after"] = receipt.DocumentRevisionAfter,
                ["created_outputs"] = receipt.CreatedOutputs,
                ["modified_entities"] = receipt.ModifiedEntities,
                ["checkpoint"] = result.CheckpointV2 is not null
                    ? new
                    {
                        schema_version = result.CheckpointV2.SchemaVersion,
                        id = result.CheckpointV2.CheckpointId,
                        digest = result.CheckpointV2.CheckpointDigest
                    }
                    : result.CreatedCheckpointV1 is not null
                        ? new
                        {
                            schema_version =
                                result.CreatedCheckpointV1.SchemaVersion,
                            id = result.CreatedCheckpointV1.CheckpointId,
                            digest =
                                result.CreatedCheckpointV1.CheckpointDigest
                        }
                        : null,
                ["duplicate"] = result.Duplicate,
                ["milestone"] = "effect_and_receipt_committed"
            });
    }

    private CadManagedHostAdmission ManagedAdmission(
        Phase8CanonicalHostCommand command,
        CadManagedSealedPlan plan) =>
        new(
            plan.Pins,
            command.CapabilityEvidence
                .Select(item => item.CapabilityKey)
                .ToHashSet(StringComparer.Ordinal),
            plan.Operations.Select(item => item.Kind)
                .ToHashSet(StringComparer.Ordinal),
            IndependentCapabilityEvidenceVerified: true,
            CreatePackEnabled: _createPackEnabled,
            CheckpointV2Enabled: _checkpointV2Enabled,
            TransformPackEnabled: _transformPackEnabled);

    private PreviewResult PreviewCore(
        Document document,
        DocumentIdentity identity,
        Phase8CanonicalHostCommand command)
    {
        IReadOnlyList<Phase8CanonicalCreatedEntity> entities;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartTransaction())
        {
            var applied = AutoCadProgramOperations.Apply(
                document.Database,
                transaction,
                command.ToCreateProgram());
            entities = CreatedEntities(command, applied.Entities, transaction);
            transaction.Abort();
        }
        var evidence = JsonSerializer.SerializeToElement(
            new
            {
                entities = entities.Select(item => new
                {
                    item.OperationId,
                    item.EntityType,
                    item.Layer,
                    item.Fingerprint
                }).ToArray()
            },
            HostProtocol.JsonOptions);
        return new(command.PreviewEvidenceDigest(evidence), entities);
    }

    private static IReadOnlyList<Phase8CanonicalCreatedEntity> CreatedEntities(
        Phase8CanonicalHostCommand command,
        IReadOnlyList<Entity> entities,
        Transaction transaction)
    {
        var operationIds = command.ToCreateProgram().Operations
            .Where(operation => operation is not EnsureLayerOperation)
            .Select(operation => operation.OperationId)
            .ToArray();
        if (operationIds.Length != entities.Count)
        {
            throw new ProtocolValidationException(
                "commit_validation_failed",
                "Created entity count differs from sealed operations.");
        }
        return entities.Select((entity, index) =>
            new Phase8CanonicalCreatedEntity(
                operationIds[index],
                entity.Handle.ToString(),
                entity.GetRXClass().DxfName,
                entity.Layer,
                AutoCadProgramOperations.EntityFingerprint(entity, transaction)))
            .ToArray();
    }

    private static object CommitResult(
        Phase8CanonicalHostCommand command,
        Phase8CanonicalCreateReceipt receipt,
        bool duplicate) =>
        CommonResult(
            command,
            new Dictionary<string, object?>(StringComparer.Ordinal)
            {
                ["receipt_id"] = receipt.ReceiptId,
                ["receipt_digest"] = receipt.ReceiptDigest,
                ["effect_identity_digest"] = receipt.EffectIdentityDigest,
                ["document_revision_before"] =
                    receipt.DocumentRevisionBefore,
                ["document_revision_after"] =
                    receipt.DocumentRevisionAfter,
                ["created_entities"] = receipt.CreatedEntities,
                ["checkpoint"] = new
                {
                    schema_version =
                        Phase8CanonicalCreatedCheckpoint.Version,
                    id = receipt.CheckpointId,
                    digest = receipt.CheckpointDigest
                },
                ["duplicate"] = duplicate,
                ["milestone"] = "effect_and_receipt_committed"
            });

    private static object CommonResult(
        Phase8CanonicalHostCommand command,
        Dictionary<string, object?> values)
    {
        values["execution_plan_digest"] = command.Digests.ExecutionPlanDigest;
        values["effect_manifest_digest"] = command.Digests.EffectManifestDigest;
        values["target_refs_digest"] = command.Digests.TargetRefsDigest;
        values["hard_budgets_digest"] = command.HardBudgetsDigest;
        values["rollout_policy_digest"] = command.RolloutPolicyDigest;
        return values;
    }

    private static void AssertDuplicate(
        Phase8CanonicalCreateReceipt receipt,
        Phase8CanonicalHostCommand command)
    {
        if (receipt.ExecutionPlanDigest != command.Digests.ExecutionPlanDigest ||
            receipt.EffectManifestDigest != command.Digests.EffectManifestDigest ||
            receipt.TargetRefsDigest != command.Digests.TargetRefsDigest ||
            receipt.EffectIdentityDigest != command.EffectIdentityDigest() ||
            receipt.DocumentId != command.DocumentId ||
            receipt.DocumentRevisionBefore != command.ExpectedDocumentRevision ||
            receipt.CapabilityEvidenceDigests.Count !=
                command.CapabilityEvidence.Count ||
            !receipt.CapabilityEvidenceDigests.SequenceEqual(
                command.CapabilityEvidence.Select(item => item.EvidenceDigest)
                    .Order(StringComparer.Ordinal)))
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "Receipt ID was reused for another canonical Phase 8 command.");
        }
    }

    private static object QueryReceipt(Document document, string receiptId)
    {
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var managed = DrawingProgramLedger.FindPhase8Commit(
                document.Database,
                transaction,
                receiptId);
            if (managed is not null)
            {
                var value = managed.Receipt;
                return new
                {
                    found = true,
                    receipt_id = value.ReceiptId,
                    receipt_digest = managed.ReceiptDigest,
                    execution_plan_digest = value.PlanDigest,
                    effect_manifest_digest = value.EffectDigest,
                    effect_identity_digest = value.EffectIdentityDigest,
                    document_id = value.DocumentId,
                    document_revision_after = value.DocumentRevisionAfter,
                    rollback_eligible =
                        value.CheckpointV2Id is not null ||
                        value.CheckpointV1Id is not null,
                    checkpoint_id =
                        value.CheckpointV2Id ?? value.CheckpointV1Id,
                    checkpoint_digest =
                        value.CheckpointV2Digest ?? value.CheckpointV1Digest
                };
            }
            var receipt = DrawingProgramLedger.FindPhase8CanonicalReceipt(
                document.Database,
                transaction,
                receiptId);
            return receipt is null
                ? new
                {
                    found = false,
                    receipt_id = receiptId,
                    rollback_eligible = false
                }
                : new
                {
                    found = true,
                    receipt_id = receipt.ReceiptId,
                    receipt_digest = receipt.ReceiptDigest,
                    execution_plan_digest = receipt.ExecutionPlanDigest,
                    effect_manifest_digest = receipt.EffectManifestDigest,
                    effect_identity_digest = receipt.EffectIdentityDigest,
                    document_id = receipt.DocumentId,
                    document_revision_after = receipt.DocumentRevisionAfter,
                    rollback_eligible = true,
                    checkpoint_id = receipt.CheckpointId,
                    checkpoint_digest = receipt.CheckpointDigest
                };
        }
    }

    private static object QueryCheckpoint(Document document, string checkpointId)
    {
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var checkpointV2 = DrawingProgramLedger.FindCheckpointV2(
                document.Database,
                transaction,
                checkpointId);
            if (checkpointV2 is not null)
            {
                return new
                {
                    found = true,
                    schema_version = checkpointV2.SchemaVersion,
                    checkpoint_id = checkpointV2.CheckpointId,
                    checkpoint_digest = checkpointV2.CheckpointDigest,
                    receipt_id = checkpointV2.ReceiptId,
                    execution_plan_digest = checkpointV2.PlanDigest,
                    effect_manifest_digest = checkpointV2.EffectDigest,
                    document_id = checkpointV2.DocumentId,
                    document_revision_before =
                        checkpointV2.DocumentRevisionBefore,
                    document_revision_after =
                        checkpointV2.DocumentRevisionAfter,
                    modified_entity_count = checkpointV2.RestoreEntries.Count
                };
            }
            var created = DrawingProgramLedger.FindPhase8CreatedCheckpoint(
                document.Database,
                transaction,
                checkpointId);
            if (created is not null)
            {
                return new
                {
                    found = true,
                    schema_version = created.SchemaVersion,
                    checkpoint_id = created.CheckpointId,
                    checkpoint_digest = created.CheckpointDigest,
                    receipt_id = created.ReceiptId,
                    effect_identity_digest = created.EffectIdentityDigest,
                    execution_plan_digest = created.PlanDigest,
                    effect_manifest_digest = created.EffectDigest,
                    document_id = created.DocumentId,
                    document_revision_before =
                        created.DocumentRevisionBefore,
                    document_revision_after =
                        created.DocumentRevisionAfter,
                    created_entity_count = created.CreatedOutputs.Count
                };
            }
            var checkpoint = DrawingProgramLedger.FindPhase8CanonicalCheckpoint(
                document.Database,
                transaction,
                checkpointId);
            return checkpoint is null
                ? new { found = false, checkpoint_id = checkpointId }
                : new
                {
                    found = true,
                    schema_version = checkpoint.SchemaVersion,
                    checkpoint_id = checkpoint.CheckpointId,
                    checkpoint_digest = checkpoint.CheckpointDigest,
                    receipt_id = checkpoint.ReceiptId,
                    effect_identity_digest = checkpoint.EffectIdentityDigest,
                    execution_plan_digest = checkpoint.ExecutionPlanDigest,
                    effect_manifest_digest = checkpoint.EffectManifestDigest,
                    document_id = checkpoint.DocumentId,
                    document_revision_before =
                        checkpoint.DocumentRevisionBefore,
                    document_revision_after =
                        checkpoint.DocumentRevisionAfter,
                    created_entity_count = checkpoint.CreatedEntities.Count
                };
        }
    }

    private object PreviewRollback(
        Document document,
        DocumentIdentity identity,
        CadRollbackRequest request)
    {
        if (TryLoadManagedCheckpoint(
                document,
                request.CheckpointId!,
                request.CheckpointDigest!,
                out var managedV2,
                out var managedCreated))
        {
            return PreviewManagedRollback(
                document,
                identity,
                request,
                managedV2,
                managedCreated);
        }
        var checkpoint = LoadCanonicalCheckpoint(
            document,
            request.CheckpointId!,
            request.CheckpointDigest!);
        var revision = Revision(identity);
        if (checkpoint.DocumentRevisionAfter != revision)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Drawing changed after canonical Phase 8 commit.");
        }
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            foreach (var expected in checkpoint.CreatedEntities)
            {
                _ = RequireExactCreatedEntity(
                    document.Database,
                    transaction,
                    expected);
            }
        }
        var planDigest = CanonicalRollbackPlanDigest(
            request,
            checkpoint,
            revision);
        var record = new CanonicalRollbackPlan(
            request.RollbackPlanId!,
            planDigest,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            request.RollbackExecutionDigest!,
            request.ExpiresAt!,
            revision);
        if (_rollbackPlans.TryGetValue(record.PlanId, out var existing) &&
            existing != record)
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "Rollback plan ID was reused with another checkpoint.");
        }
        _rollbackPlans[record.PlanId] = record;
        return new
        {
            rollback_plan_id = record.PlanId,
            rollback_plan_digest = record.PlanDigest,
            checkpoint_id = record.CheckpointId,
            checkpoint_digest = record.CheckpointDigest,
            checkpoint_schema = checkpoint.SchemaVersion,
            document_revision = revision,
            conflict_count = 0,
            can_commit = true,
            expires_at = record.ExpiresAt,
            drawing_unchanged = true
        };
    }

    private object CommitRollback(
        Document document,
        DocumentIdentity identity,
        CadRollbackRequest request)
    {
        if (_managedRollbackPlans.ContainsKey(request.RollbackPlanId!))
        {
            return CommitManagedRollback(document, identity, request);
        }
        if (!_rollbackPlans.TryGetValue(request.RollbackPlanId!, out var plan) ||
            plan.PlanDigest != request.RollbackPlanDigest ||
            plan.CheckpointId != request.CheckpointId ||
            plan.CheckpointDigest != request.CheckpointDigest ||
            plan.ExecutionDigest != request.RollbackExecutionDigest ||
            plan.ExpiresAt != request.ExpiresAt ||
            DateTimeOffset.Parse(plan.ExpiresAt, CultureInfo.InvariantCulture) <=
                DateTimeOffset.UtcNow)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Rollback commit differs from exact canonical rollback preview.");
        }
        var checkpoint = LoadCanonicalCheckpoint(
            document,
            plan.CheckpointId,
            plan.CheckpointDigest);
        var revisionBefore = Revision(identity);
        if (revisionBefore != plan.DocumentRevision)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Drawing revision changed after rollback preview.");
        }
        var revisionAfter = IncrementRevision(revisionBefore);
        Phase8CanonicalRollbackReceipt receipt;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartTransaction())
        {
            var existing =
                DrawingProgramLedger.FindPhase8CanonicalRollbackReceipt(
                    document.Database,
                    transaction,
                    request.RollbackReceiptId!);
            if (existing is not null)
            {
                if (existing.CheckpointId != checkpoint.CheckpointId ||
                    existing.CheckpointDigest != checkpoint.CheckpointDigest ||
                    existing.RollbackPlanDigest != plan.PlanDigest)
                {
                    throw new ProtocolValidationException(
                        "duplicate_payload_mismatch",
                        "Rollback receipt ID was reused for another checkpoint.");
                }
                return RollbackCommitResult(existing, duplicate: true);
            }
            foreach (var expected in checkpoint.CreatedEntities)
            {
                var entity = RequireExactCreatedEntity(
                    document.Database,
                    transaction,
                    expected);
                entity.UpgradeOpen();
                entity.Erase();
            }
            receipt = Phase8CanonicalRollbackReceipt.Create(
                request.RollbackReceiptId!,
                checkpoint,
                plan.PlanId,
                plan.PlanDigest,
                plan.ExecutionDigest,
                revisionAfter,
                DateTimeOffset.UtcNow);
            DrawingProgramLedger.AddPhase8CanonicalRollbackReceipt(
                document.Database,
                transaction,
                receipt);
            transaction.Commit();
        }
        identity.Revision.Record(
            DocumentEventKind.ObjectModified,
            DateTimeOffset.UtcNow,
            receipt.RollbackReceiptId,
            changesContent: true);
        if (Revision(identity) != revisionAfter)
        {
            throw new ProtocolValidationException(
                "rollback_validation_failed",
                "Canonical rollback revision differs from durable receipt.");
        }
        return RollbackCommitResult(receipt, duplicate: false);
    }

    private static object ValidateRollback(
        Document document,
        string rollbackReceiptId)
    {
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var restored = DrawingProgramLedger.FindPhase8RestoreReceipt(
                document.Database,
                transaction,
                rollbackReceiptId);
            if (restored is not null)
            {
                var restoreFailures = restored.RestoredEntities
                    .Where(item =>
                        !TryObjectId(document.Database, item.EntityId, out var objectId) ||
                        transaction.GetObject(objectId, OpenMode.ForRead) is not Entity entity ||
                        Phase8ManagedOperationPack.EntityFingerprint(
                            entity,
                            transaction) != item.FingerprintAfter)
                    .Select(item => $"restore_result_changed:{item.EntityId}")
                    .ToArray();
                return new
                {
                    rollback_receipt_id = restored.RestoreReceiptId,
                    valid = restoreFailures.Length == 0,
                    failures = restoreFailures,
                    checkpoint_id = restored.CheckpointId,
                    checkpoint_digest = restored.CheckpointDigest,
                    execution_plan_digest = restored.PlanDigest,
                    effect_manifest_digest = restored.EffectDigest,
                    document_revision_after = restored.DocumentRevisionAfter
                };
            }
            var created = DrawingProgramLedger.FindPhase8CreatedRollbackReceipt(
                document.Database,
                transaction,
                rollbackReceiptId);
            if (created is not null)
            {
                var removalFailures = created.RemovedOutputs
                    .Where(item => TryObjectId(
                        document.Database,
                        item.EntityId,
                        out _))
                    .Select(item => $"entity_still_exists:{item.EntityId}")
                    .ToArray();
                return new
                {
                    rollback_receipt_id = created.RollbackReceiptId,
                    valid = removalFailures.Length == 0,
                    failures = removalFailures,
                    checkpoint_id = created.CheckpointId,
                    checkpoint_digest = created.CheckpointDigest,
                    execution_plan_digest = (string?)null,
                    effect_manifest_digest = (string?)null,
                    document_revision_after = created.DocumentRevisionAfter
                };
            }
            var receipt =
                DrawingProgramLedger.FindPhase8CanonicalRollbackReceipt(
                    document.Database,
                    transaction,
                    rollbackReceiptId)
                ?? throw new ProtocolValidationException(
                    "program_invalid",
                    "Canonical rollback receipt was not found.");
            var failures = receipt.RemovedEntities
                .Where(entity => TryObjectId(
                    document.Database,
                    entity.EntityId,
                    out _))
                .Select(entity => $"entity_still_exists:{entity.EntityId}")
                .ToArray();
            return new
            {
                rollback_receipt_id = receipt.RollbackReceiptId,
                valid = failures.Length == 0,
                failures,
                checkpoint_id = receipt.CheckpointId,
                checkpoint_digest = receipt.CheckpointDigest,
                execution_plan_digest = receipt.ExecutionPlanDigest,
                effect_manifest_digest = receipt.EffectManifestDigest,
                document_revision_after = receipt.DocumentRevisionAfter
            };
        }
    }

    private static object RollbackCommitResult(
        Phase8CanonicalRollbackReceipt receipt,
        bool duplicate) =>
        new
        {
            rollback_receipt_id = receipt.RollbackReceiptId,
            receipt_digest = receipt.ReceiptDigest,
            checkpoint_id = receipt.CheckpointId,
            checkpoint_digest = receipt.CheckpointDigest,
            execution_plan_digest = receipt.ExecutionPlanDigest,
            effect_manifest_digest = receipt.EffectManifestDigest,
            document_revision_before = receipt.DocumentRevisionBefore,
            document_revision_after = receipt.DocumentRevisionAfter,
            removed_entities = receipt.RemovedEntities,
            duplicate,
            milestone = "rollback_effect_and_receipt_committed"
        };

    private static Phase8CanonicalCreatedCheckpoint LoadCanonicalCheckpoint(
        Document document,
        string checkpointId,
        string checkpointDigest)
    {
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var checkpoint = DrawingProgramLedger.FindPhase8CanonicalCheckpoint(
                document.Database,
                transaction,
                checkpointId)
                ?? throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Canonical checkpoint ID was not found in trusted drawing ledger.");
            if (checkpoint.CheckpointDigest != checkpointDigest)
            {
                throw new ProtocolValidationException(
                    "rollback_conflict",
                    "Pinned checkpoint digest differs from drawing ledger.");
            }
            return checkpoint;
        }
    }

    private static bool TryLoadManagedCheckpoint(
        Document document,
        string checkpointId,
        string checkpointDigest,
        out CadRollbackCheckpointV2? checkpointV2,
        out CadCreatedOutputCheckpointV1? createdCheckpoint)
    {
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            checkpointV2 = DrawingProgramLedger.FindCheckpointV2(
                document.Database,
                transaction,
                checkpointId);
            createdCheckpoint = checkpointV2 is null
                ? DrawingProgramLedger.FindPhase8CreatedCheckpoint(
                    document.Database,
                    transaction,
                    checkpointId)
                : null;
        }
        var actualDigest =
            checkpointV2?.CheckpointDigest ?? createdCheckpoint?.CheckpointDigest;
        if (actualDigest is null)
        {
            return false;
        }
        if (actualDigest != checkpointDigest)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Pinned checkpoint digest differs from drawing ledger.");
        }
        return true;
    }

    private object PreviewManagedRollback(
        Document document,
        DocumentIdentity identity,
        CadRollbackRequest request,
        CadRollbackCheckpointV2? checkpointV2,
        CadCreatedOutputCheckpointV1? createdCheckpoint)
    {
        var revision = Revision(identity);
        var expectedRevision =
            checkpointV2?.DocumentRevisionAfter ??
            createdCheckpoint!.DocumentRevisionAfter;
        if (revision != expectedRevision)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Drawing changed after the managed Phase 8 commit.");
        }
        var receiptId = checkpointV2?.ReceiptId ?? createdCheckpoint!.ReceiptId;
        IReadOnlyList<string> failures;
        using (document.LockDocument())
        using (var transaction =
               document.Database.TransactionManager.StartOpenCloseTransaction())
        {
            var commit = DrawingProgramLedger.FindPhase8Commit(
                document.Database,
                transaction,
                receiptId)
                ?? throw new ProtocolValidationException(
                    "ledger_corrupt",
                    "Managed Phase 8 commit receipt is missing.");
            failures = Phase8ManagedOperationPack.Validate(
                document.Database,
                transaction,
                commit);
        }
        if (failures.Count != 0)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Managed Phase 8 effect changed after commit.");
        }
        var kind = checkpointV2 is null ? "created_outputs" : "restore_v2";
        var planDigest = ManagedRollbackPlanDigest(
            request,
            request.CheckpointId!,
            request.CheckpointDigest!,
            revision,
            kind);
        var plan = new ManagedRollbackPlan(
            request.RollbackPlanId!,
            planDigest,
            request.CheckpointId!,
            request.CheckpointDigest!,
            request.RollbackExecutionDigest!,
            request.ExpiresAt!,
            revision,
            kind);
        if (_managedRollbackPlans.TryGetValue(plan.PlanId, out var existing) &&
            existing != plan)
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "Rollback plan ID was reused with another managed checkpoint.");
        }
        _managedRollbackPlans[plan.PlanId] = plan;
        var rollbackReceiptId = checkpointV2 is not null
            ? CadManagedRestoreReceipt.BuildReceiptId(checkpointV2)
            : CadCreatedOutputRollbackReceiptV1.BuildReceiptId(createdCheckpoint!);
        return new
        {
            rollback_plan_id = plan.PlanId,
            rollback_plan_digest = plan.PlanDigest,
            rollback_receipt_id = rollbackReceiptId,
            checkpoint_id = plan.CheckpointId,
            checkpoint_digest = plan.CheckpointDigest,
            checkpoint_schema =
                checkpointV2?.SchemaVersion ?? createdCheckpoint!.SchemaVersion,
            document_revision = revision,
            conflict_count = 0,
            can_commit = true,
            expires_at = plan.ExpiresAt,
            drawing_unchanged = true
        };
    }

    private object CommitManagedRollback(
        Document document,
        DocumentIdentity identity,
        CadRollbackRequest request)
    {
        var plan = _managedRollbackPlans[request.RollbackPlanId!];
        if (plan.PlanDigest != request.RollbackPlanDigest ||
            plan.CheckpointId != request.CheckpointId ||
            plan.CheckpointDigest != request.CheckpointDigest ||
            plan.ExecutionDigest != request.RollbackExecutionDigest ||
            plan.ExpiresAt != request.ExpiresAt ||
            DateTimeOffset.Parse(plan.ExpiresAt, CultureInfo.InvariantCulture) <=
                DateTimeOffset.UtcNow)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Rollback commit differs from exact managed rollback preview.");
        }
        if (!TryLoadManagedCheckpoint(
                document,
                plan.CheckpointId,
                plan.CheckpointDigest,
                out var checkpointV2,
                out var createdCheckpoint))
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Managed checkpoint ID was not found in trusted drawing ledger.");
        }
        var expectedReceiptId = checkpointV2 is not null
            ? CadManagedRestoreReceipt.BuildReceiptId(checkpointV2)
            : CadCreatedOutputRollbackReceiptV1.BuildReceiptId(createdCheckpoint!);
        if (request.RollbackReceiptId != expectedReceiptId)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Rollback receipt ID differs from the checkpoint effect identity.");
        }
        var revisionBefore = Revision(identity);
        var revisionAfter = IncrementRevision(revisionBefore);
        var pins = checkpointV2?.RuntimePins ?? createdCheckpoint!.Pins;
        var admission = new CadManagedHostAdmission(
            pins,
            new HashSet<string>(StringComparer.Ordinal),
            new HashSet<string>(StringComparer.Ordinal),
            IndependentCapabilityEvidenceVerified: true,
            CreatePackEnabled: _createPackEnabled,
            CheckpointV2Enabled: _checkpointV2Enabled,
            TransformPackEnabled: _transformPackEnabled);

        object result;
        bool duplicate;
        using (identity.Revision.SuppressChanges())
        using (document.LockDocument())
        {
            if (checkpointV2 is not null)
            {
                var restored = Phase8ManagedOperationPack.Restore(
                    document.Database,
                    checkpointV2,
                    plan.CheckpointDigest,
                    revisionBefore,
                    revisionAfter,
                    admission,
                    DateTimeOffset.UtcNow);
                duplicate = restored.Duplicate;
                result = new
                {
                    rollback_receipt_id = restored.Receipt.RestoreReceiptId,
                    receipt_digest = restored.Receipt.ReceiptDigest,
                    checkpoint_id = restored.Receipt.CheckpointId,
                    checkpoint_digest = restored.Receipt.CheckpointDigest,
                    execution_plan_digest = restored.Receipt.PlanDigest,
                    effect_manifest_digest = restored.Receipt.EffectDigest,
                    document_revision_before =
                        restored.Receipt.DocumentRevisionBefore,
                    document_revision_after =
                        restored.Receipt.DocumentRevisionAfter,
                    restored_entities = restored.Receipt.RestoredEntities,
                    duplicate = restored.Duplicate,
                    milestone = "rollback_effect_and_receipt_committed"
                };
            }
            else
            {
                var rolledBack = Phase8ManagedOperationPack.RollbackCreatedOutputs(
                    document.Database,
                    createdCheckpoint!,
                    plan.CheckpointDigest,
                    revisionBefore,
                    revisionAfter,
                    admission,
                    DateTimeOffset.UtcNow);
                duplicate = rolledBack.Duplicate;
                result = new
                {
                    rollback_receipt_id =
                        rolledBack.Receipt.RollbackReceiptId,
                    receipt_digest = rolledBack.Receipt.ReceiptDigest,
                    checkpoint_id = rolledBack.Receipt.CheckpointId,
                    checkpoint_digest = rolledBack.Receipt.CheckpointDigest,
                    execution_plan_digest = createdCheckpoint!.PlanDigest,
                    effect_manifest_digest = createdCheckpoint.EffectDigest,
                    document_revision_before =
                        rolledBack.Receipt.DocumentRevisionBefore,
                    document_revision_after =
                        rolledBack.Receipt.DocumentRevisionAfter,
                    removed_entities = rolledBack.Receipt.RemovedOutputs,
                    duplicate = rolledBack.Duplicate,
                    milestone = "rollback_effect_and_receipt_committed"
                };
            }
        }
        if (!duplicate)
        {
            identity.Revision.Record(
                DocumentEventKind.ObjectModified,
                DateTimeOffset.UtcNow,
                request.RollbackReceiptId,
                changesContent: true);
            if (Revision(identity) != revisionAfter)
            {
                throw new ProtocolValidationException(
                    "rollback_validation_failed",
                    "Managed rollback revision differs from durable receipt.");
            }
        }
        return result;
    }

    private static string ManagedRollbackPlanDigest(
        CadRollbackRequest request,
        string checkpointId,
        string checkpointDigest,
        string revision,
        string kind)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                rollback_plan_id = request.RollbackPlanId,
                checkpoint_id = checkpointId,
                checkpoint_digest = checkpointDigest,
                rollback_execution_digest = request.RollbackExecutionDigest,
                document_revision = revision,
                expires_at = request.ExpiresAt,
                rollback_kind = kind
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static Entity RequireExactCreatedEntity(
        Database database,
        Transaction transaction,
        Phase8CanonicalCreatedEntity expected)
    {
        if (!TryObjectId(database, expected.EntityId, out var objectId) ||
            transaction.GetObject(objectId, OpenMode.ForRead) is not Entity entity ||
            entity.GetRXClass().DxfName != expected.EntityType ||
            !string.Equals(
                entity.Layer,
                expected.Layer,
                StringComparison.OrdinalIgnoreCase) ||
            AutoCadProgramOperations.EntityFingerprint(entity, transaction) !=
                expected.Fingerprint)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Created entity changed after canonical Phase 8 commit.");
        }
        var blocks = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead);
        if (entity.OwnerId != blocks[BlockTableRecord.ModelSpace] ||
            entity.GetPersistentReactorIds().Count != 0)
        {
            throw new ProtocolValidationException(
                "rollback_conflict",
                "Created entity ownership/dependency safety is unproven.");
        }
        return entity;
    }

    private static bool TryObjectId(
        Database database,
        string entityId,
        out ObjectId objectId)
    {
        try
        {
            objectId = database.GetObjectId(
                false,
                new Handle(long.Parse(
                    entityId,
                    NumberStyles.HexNumber,
                    CultureInfo.InvariantCulture)),
                0);
            return !objectId.IsNull && objectId.IsValid && !objectId.IsErased;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception)
        {
            objectId = ObjectId.Null;
            return false;
        }
    }

    private static string CanonicalRollbackPlanDigest(
        CadRollbackRequest request,
        Phase8CanonicalCreatedCheckpoint checkpoint,
        string revision)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                rollback_plan_id = request.RollbackPlanId,
                checkpoint_id = checkpoint.CheckpointId,
                checkpoint_digest = checkpoint.CheckpointDigest,
                rollback_execution_digest = request.RollbackExecutionDigest,
                document_revision = revision,
                expires_at = request.ExpiresAt,
                execution_plan_digest = checkpoint.ExecutionPlanDigest,
                effect_manifest_digest = checkpoint.EffectManifestDigest
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static string Revision(DocumentIdentity identity) =>
        identity.Revision.Snapshot(DateTimeOffset.UtcNow)
            .Revision.ToString(CultureInfo.InvariantCulture);

    private static string IncrementRevision(string revision) =>
        (long.Parse(revision, CultureInfo.InvariantCulture) + 1)
        .ToString(CultureInfo.InvariantCulture);

    private static void AssertRevision(string expected, string actual)
    {
        if (expected != actual)
        {
            throw new ProtocolValidationException(
                "stale_snapshot",
                "Drawing revision differs from canonical sealed plan.");
        }
    }

    private static int GetCommandActive()
    {
        try
        {
            return Convert.ToInt32(
                Application.GetSystemVariable("CMDACTIVE"),
                CultureInfo.InvariantCulture);
        }
        catch
        {
            return 1;
        }
    }

    private sealed record PreviewResult(
        string Digest,
        IReadOnlyList<Phase8CanonicalCreatedEntity> Entities);

    private sealed record CanonicalRollbackPlan(
        string PlanId,
        string PlanDigest,
        string CheckpointId,
        string CheckpointDigest,
        string ExecutionDigest,
        string ExpiresAt,
        string DocumentRevision);

    private sealed record ManagedRollbackPlan(
        string PlanId,
        string PlanDigest,
        string CheckpointId,
        string CheckpointDigest,
        string ExecutionDigest,
        string ExpiresAt,
        string DocumentRevision,
        string Kind);
}

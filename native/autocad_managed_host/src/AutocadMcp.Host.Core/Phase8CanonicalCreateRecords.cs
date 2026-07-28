using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AutocadMcp.Host.Core;

public sealed record Phase8CanonicalCreatedEntity(
    string OperationId,
    string EntityId,
    string EntityType,
    string Layer,
    string Fingerprint);

public sealed record Phase8CanonicalCreateReceipt(
    string SchemaVersion,
    string ReceiptId,
    string ReceiptDigest,
    string EffectIdentityDigest,
    string ExecutionPlanDigest,
    string EffectManifestDigest,
    string TargetRefsDigest,
    string ValidationProfilesDigest,
    string CheckpointStrategyDigest,
    string HardBudgetsDigest,
    string RolloutPolicyDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<Phase8CanonicalCreatedEntity> CreatedEntities,
    string CheckpointId,
    string CheckpointDigest,
    IReadOnlyList<string> CapabilityEvidenceDigests,
    string CreatedAt)
{
    public const string Version = "cad.program.receipt/1";
    internal const int MaximumRecordBytes = 120_000;

    public static Phase8CanonicalCreateReceipt Create(
        Phase8CanonicalHostCommand command,
        string documentRevisionAfter,
        IReadOnlyList<Phase8CanonicalCreatedEntity> entities,
        string checkpointId,
        string checkpointDigest,
        DateTimeOffset now)
    {
        var approval = command.Approval ??
            throw Invalid("Canonical create receipt requires trusted approval.");
        var value = new Phase8CanonicalCreateReceipt(
            Version,
            approval.ReceiptId,
            EmptyDigest,
            command.EffectIdentityDigest(),
            command.Digests.ExecutionPlanDigest,
            command.Digests.EffectManifestDigest,
            command.Digests.TargetRefsDigest,
            command.ValidationProfilesDigest,
            command.CheckpointStrategyDigest,
            command.HardBudgetsDigest,
            command.RolloutPolicyDigest,
            command.DocumentId,
            command.ExpectedDocumentRevision,
            documentRevisionAfter,
            entities,
            checkpointId,
            checkpointDigest,
            command.CapabilityEvidence.Select(item => item.EvidenceDigest)
                .Order(StringComparer.Ordinal).ToArray(),
            now.ToUniversalTime().ToString("O"));
        value.Validate(includeDigest: false);
        return value with { ReceiptDigest = value.ComputeDigest() };
    }

    public string Serialize()
    {
        Validate(includeDigest: true);
        var json = JsonSerializer.Serialize(this, HostProtocol.JsonOptions);
        if (Encoding.UTF8.GetByteCount(json) > MaximumRecordBytes)
        {
            throw Invalid("Canonical create receipt exceeds ledger bound.");
        }
        return json;
    }

    public static Phase8CanonicalCreateReceipt Parse(string json)
    {
        try
        {
            using var document = StrictDocument(json);
            RequireExact(
                document.RootElement,
                [
                    "schema_version", "receipt_id", "receipt_digest",
                    "effect_identity_digest", "execution_plan_digest",
                    "effect_manifest_digest", "target_refs_digest",
                    "validation_profiles_digest", "checkpoint_strategy_digest",
                    "hard_budgets_digest", "rollout_policy_digest", "document_id",
                    "document_revision_before", "document_revision_after",
                    "created_entities", "checkpoint_id", "checkpoint_digest",
                    "capability_evidence_digests", "created_at"
                ]);
            foreach (var entity in document.RootElement.GetProperty(
                         "created_entities").EnumerateArray())
            {
                RequireExact(
                    entity,
                    [
                        "operation_id", "entity_id", "entity_type", "layer",
                        "fingerprint"
                    ]);
            }
            var value = JsonSerializer.Deserialize<Phase8CanonicalCreateReceipt>(
                document.RootElement.GetRawText(),
                HostProtocol.JsonOptions) ?? throw Invalid("Receipt is empty.");
            value.Validate(includeDigest: true);
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception error) when (
            error is JsonException or InvalidOperationException or FormatException)
        {
            throw Invalid("Canonical create receipt is malformed.");
        }
    }

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Version ||
            CreatedEntities.Count is < 1 or > Phase8Contract.MaxExpandedOperations ||
            !DateTimeOffset.TryParse(CreatedAt, out _) ||
            CreatedEntities.Select(item => item.EntityId)
                .Distinct(StringComparer.Ordinal).Count() != CreatedEntities.Count ||
            CapabilityEvidenceDigests.Count is < 1 or > 64)
        {
            throw Invalid("Canonical create receipt binding is invalid.");
        }
        RequireId(ReceiptId, 128);
        RequireId(DocumentId, 128);
        RequireId(CheckpointId, 64);
        foreach (var digest in new[]
        {
            EffectIdentityDigest, ExecutionPlanDigest, EffectManifestDigest,
            TargetRefsDigest, ValidationProfilesDigest, CheckpointStrategyDigest,
            HardBudgetsDigest, RolloutPolicyDigest, CheckpointDigest
        }.Concat(CapabilityEvidenceDigests))
        {
            RequireDigest(digest);
        }
        foreach (var entity in CreatedEntities)
        {
            RequireId(entity.OperationId, 128);
            RequireHandle(entity.EntityId);
            RequireId(entity.EntityType, 64);
            RequireId(entity.Layer, 255);
            RequireDigest(entity.Fingerprint);
        }
        if (includeDigest)
        {
            RequireDigest(ReceiptDigest);
            if (ReceiptDigest != ComputeDigest())
            {
                throw Invalid("Canonical create receipt digest is invalid.");
            }
        }
    }

    private string ComputeDigest()
    {
        using var document = JsonSerializer.SerializeToDocument(
            this with { ReceiptDigest = EmptyDigest },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    internal static JsonDocument StrictDocument(string json) =>
        Encoding.UTF8.GetByteCount(json) > MaximumRecordBytes
            ? throw Invalid("Canonical durable record exceeds ledger bound.")
            : JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 16
            });

    internal static void RequireExact(
        JsonElement value,
        IEnumerable<string> fields)
    {
        var expected = fields.ToHashSet(StringComparer.Ordinal);
        if (value.ValueKind != JsonValueKind.Object ||
            value.EnumerateObject().Count() != expected.Count ||
            value.EnumerateObject().Any(item => !expected.Contains(item.Name)))
        {
            throw Invalid("Durable record has unknown or missing fields.");
        }
    }

    internal static void RequireId(string value, int maximum)
    {
        if (string.IsNullOrWhiteSpace(value) ||
            value.Length > maximum ||
            value.Any(char.IsControl))
        {
            throw Invalid("Durable record identifier is invalid.");
        }
    }

    internal static void RequireDigest(string value)
    {
        if (value.Length != 71 ||
            !value.StartsWith("sha256:", StringComparison.Ordinal) ||
            value[7..].Any(character =>
                !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw Invalid("Durable record digest is invalid.");
        }
    }

    internal static void RequireHandle(string value)
    {
        if (value.Length is < 1 or > 32 ||
            value.Any(character =>
                !Uri.IsHexDigit(character) || char.IsLower(character)))
        {
            throw Invalid("Durable entity handle is invalid.");
        }
    }

    internal static ProtocolValidationException Invalid(string message) =>
        new("ledger_corrupt", message);

    internal const string EmptyDigest =
        "sha256:0000000000000000000000000000000000000000000000000000000000000000";
}

public sealed record Phase8CanonicalCreatedCheckpoint(
    string SchemaVersion,
    string CheckpointId,
    string CheckpointDigest,
    string ReceiptId,
    string EffectIdentityDigest,
    string ExecutionPlanDigest,
    string EffectManifestDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<Phase8CanonicalCreatedEntity> CreatedEntities,
    string PackageHash,
    string OperationRegistryHash,
    string RolloutPolicyDigest,
    string CreatedAt)
{
    public const string Version = "cad.rollback.checkpoint/1-created-entities";

    public static string BuildId(string receiptId)
    {
        Phase8CanonicalCreateReceipt.RequireId(receiptId, 128);
        var hash = Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(receiptId))).ToLowerInvariant();
        return $"AUTOCAD_MCP_PHASE8_CP_{hash[..32]}";
    }

    public static Phase8CanonicalCreatedCheckpoint Create(
        Phase8CanonicalHostCommand command,
        string documentRevisionAfter,
        IReadOnlyList<Phase8CanonicalCreatedEntity> entities,
        DateTimeOffset now)
    {
        var approval = command.Approval ??
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Checkpoint requires trusted approval.");
        var pins = command.ExecutionPlan.GetProperty("execution_pins");
        var value = new Phase8CanonicalCreatedCheckpoint(
            Version,
            BuildId(approval.ReceiptId),
            Phase8CanonicalCreateReceipt.EmptyDigest,
            approval.ReceiptId,
            command.EffectIdentityDigest(),
            command.Digests.ExecutionPlanDigest,
            command.Digests.EffectManifestDigest,
            command.DocumentId,
            command.ExpectedDocumentRevision,
            documentRevisionAfter,
            entities,
            pins.GetProperty("package_hash").GetString()!,
            pins.GetProperty("operation_registry_hash").GetString()!,
            command.RolloutPolicyDigest,
            now.ToUniversalTime().ToString("O"));
        value.Validate(includeDigest: false);
        return value with { CheckpointDigest = value.ComputeDigest() };
    }

    public string Serialize()
    {
        Validate(includeDigest: true);
        var json = JsonSerializer.Serialize(this, HostProtocol.JsonOptions);
        if (Encoding.UTF8.GetByteCount(json) >
            Phase8CanonicalCreateReceipt.MaximumRecordBytes)
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical checkpoint exceeds ledger bound.");
        }
        return json;
    }

    public static Phase8CanonicalCreatedCheckpoint Parse(string json)
    {
        try
        {
            using var document = Phase8CanonicalCreateReceipt.StrictDocument(json);
            Phase8CanonicalCreateReceipt.RequireExact(
                document.RootElement,
                [
                    "schema_version", "checkpoint_id", "checkpoint_digest",
                    "receipt_id", "effect_identity_digest",
                    "execution_plan_digest", "effect_manifest_digest",
                    "document_id", "document_revision_before",
                    "document_revision_after", "created_entities", "package_hash",
                    "operation_registry_hash", "rollout_policy_digest", "created_at"
                ]);
            foreach (var entity in document.RootElement.GetProperty(
                         "created_entities").EnumerateArray())
            {
                Phase8CanonicalCreateReceipt.RequireExact(
                    entity,
                    [
                        "operation_id", "entity_id", "entity_type", "layer",
                        "fingerprint"
                    ]);
            }
            var value = JsonSerializer.Deserialize<Phase8CanonicalCreatedCheckpoint>(
                document.RootElement.GetRawText(),
                HostProtocol.JsonOptions)
                ?? throw Phase8CanonicalCreateReceipt.Invalid("Checkpoint is empty.");
            value.Validate(includeDigest: true);
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception error) when (
            error is JsonException or InvalidOperationException or FormatException)
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical checkpoint is malformed.");
        }
    }

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Version ||
            CheckpointId != BuildId(ReceiptId) ||
            CreatedEntities.Count is < 1 or > Phase8Contract.MaxExpandedOperations ||
            CreatedEntities.Select(item => item.EntityId)
                .Distinct(StringComparer.Ordinal).Count() != CreatedEntities.Count ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical checkpoint binding is invalid.");
        }
        Phase8CanonicalCreateReceipt.RequireId(ReceiptId, 128);
        Phase8CanonicalCreateReceipt.RequireId(DocumentId, 128);
        foreach (var entity in CreatedEntities)
        {
            Phase8CanonicalCreateReceipt.RequireId(entity.OperationId, 128);
            Phase8CanonicalCreateReceipt.RequireHandle(entity.EntityId);
            Phase8CanonicalCreateReceipt.RequireId(entity.EntityType, 64);
            Phase8CanonicalCreateReceipt.RequireId(entity.Layer, 255);
            Phase8CanonicalCreateReceipt.RequireDigest(entity.Fingerprint);
        }
        foreach (var digest in new[]
        {
            EffectIdentityDigest, ExecutionPlanDigest,
            EffectManifestDigest, PackageHash, OperationRegistryHash,
            RolloutPolicyDigest
        })
        {
            Phase8CanonicalCreateReceipt.RequireDigest(digest);
        }
        if (includeDigest)
        {
            Phase8CanonicalCreateReceipt.RequireDigest(CheckpointDigest);
            if (CheckpointDigest != ComputeDigest())
            {
                throw Phase8CanonicalCreateReceipt.Invalid(
                    "Canonical checkpoint digest is invalid.");
            }
        }
    }

    private string ComputeDigest()
    {
        using var document = JsonSerializer.SerializeToDocument(
            this with { CheckpointDigest = Phase8CanonicalCreateReceipt.EmptyDigest },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }
}

public sealed record Phase8CanonicalRollbackReceipt(
    string SchemaVersion,
    string RollbackReceiptId,
    string ReceiptDigest,
    string CheckpointId,
    string CheckpointDigest,
    string OriginalReceiptId,
    string EffectIdentityDigest,
    string ExecutionPlanDigest,
    string EffectManifestDigest,
    string RollbackPlanId,
    string RollbackPlanDigest,
    string RollbackExecutionDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<Phase8CanonicalCreatedEntity> RemovedEntities,
    string CreatedAt)
{
    public const string Version = "cad.rollback.receipt/1-phase8-created";

    public static Phase8CanonicalRollbackReceipt Create(
        string rollbackReceiptId,
        Phase8CanonicalCreatedCheckpoint checkpoint,
        string rollbackPlanId,
        string rollbackPlanDigest,
        string rollbackExecutionDigest,
        string documentRevisionAfter,
        DateTimeOffset now)
    {
        if (!rollbackReceiptId.StartsWith(
                "AUTOCAD_MCP_PHASE8_RB_",
                StringComparison.Ordinal))
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical rollback receipt ID is outside its namespace.");
        }
        var value = new Phase8CanonicalRollbackReceipt(
            Version,
            rollbackReceiptId,
            Phase8CanonicalCreateReceipt.EmptyDigest,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            checkpoint.ReceiptId,
            checkpoint.EffectIdentityDigest,
            checkpoint.ExecutionPlanDigest,
            checkpoint.EffectManifestDigest,
            rollbackPlanId,
            rollbackPlanDigest,
            rollbackExecutionDigest,
            checkpoint.DocumentId,
            checkpoint.DocumentRevisionAfter,
            documentRevisionAfter,
            checkpoint.CreatedEntities,
            now.ToUniversalTime().ToString("O"));
        value.Validate(includeDigest: false);
        return value with { ReceiptDigest = value.ComputeDigest() };
    }

    public string Serialize()
    {
        Validate(includeDigest: true);
        var json = JsonSerializer.Serialize(this, HostProtocol.JsonOptions);
        if (Encoding.UTF8.GetByteCount(json) >
            Phase8CanonicalCreateReceipt.MaximumRecordBytes)
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical rollback receipt exceeds ledger bound.");
        }
        return json;
    }

    public static Phase8CanonicalRollbackReceipt Parse(string json)
    {
        try
        {
            using var document = Phase8CanonicalCreateReceipt.StrictDocument(json);
            Phase8CanonicalCreateReceipt.RequireExact(
                document.RootElement,
                [
                    "schema_version", "rollback_receipt_id", "receipt_digest",
                    "checkpoint_id", "checkpoint_digest", "original_receipt_id",
                    "effect_identity_digest", "execution_plan_digest",
                    "effect_manifest_digest", "rollback_plan_id",
                    "rollback_plan_digest", "rollback_execution_digest",
                    "document_id", "document_revision_before",
                    "document_revision_after", "removed_entities", "created_at"
                ]);
            foreach (var entity in document.RootElement.GetProperty(
                         "removed_entities").EnumerateArray())
            {
                Phase8CanonicalCreateReceipt.RequireExact(
                    entity,
                    [
                        "operation_id", "entity_id", "entity_type", "layer",
                        "fingerprint"
                    ]);
            }
            var value = JsonSerializer.Deserialize<Phase8CanonicalRollbackReceipt>(
                document.RootElement.GetRawText(),
                HostProtocol.JsonOptions)
                ?? throw Phase8CanonicalCreateReceipt.Invalid(
                    "Rollback receipt is empty.");
            value.Validate(includeDigest: true);
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception error) when (
            error is JsonException or InvalidOperationException or FormatException)
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical rollback receipt is malformed.");
        }
    }

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Version ||
            !RollbackReceiptId.StartsWith(
                "AUTOCAD_MCP_PHASE8_RB_",
                StringComparison.Ordinal) ||
            RemovedEntities.Count is < 1 or > Phase8Contract.MaxExpandedOperations ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw Phase8CanonicalCreateReceipt.Invalid(
                "Canonical rollback receipt binding is invalid.");
        }
        Phase8CanonicalCreateReceipt.RequireId(RollbackReceiptId, 128);
        Phase8CanonicalCreateReceipt.RequireId(CheckpointId, 64);
        Phase8CanonicalCreateReceipt.RequireId(OriginalReceiptId, 128);
        Phase8CanonicalCreateReceipt.RequireId(RollbackPlanId, 128);
        Phase8CanonicalCreateReceipt.RequireId(DocumentId, 128);
        foreach (var entity in RemovedEntities)
        {
            Phase8CanonicalCreateReceipt.RequireId(entity.OperationId, 128);
            Phase8CanonicalCreateReceipt.RequireHandle(entity.EntityId);
            Phase8CanonicalCreateReceipt.RequireId(entity.EntityType, 64);
            Phase8CanonicalCreateReceipt.RequireId(entity.Layer, 255);
            Phase8CanonicalCreateReceipt.RequireDigest(entity.Fingerprint);
        }
        foreach (var digest in new[]
        {
            CheckpointDigest, EffectIdentityDigest, ExecutionPlanDigest,
            EffectManifestDigest, RollbackPlanDigest, RollbackExecutionDigest
        })
        {
            Phase8CanonicalCreateReceipt.RequireDigest(digest);
        }
        if (includeDigest)
        {
            Phase8CanonicalCreateReceipt.RequireDigest(ReceiptDigest);
            if (ReceiptDigest != ComputeDigest())
            {
                throw Phase8CanonicalCreateReceipt.Invalid(
                    "Canonical rollback receipt digest is invalid.");
            }
        }
    }

    private string ComputeDigest()
    {
        using var document = JsonSerializer.SerializeToDocument(
            this with { ReceiptDigest = Phase8CanonicalCreateReceipt.EmptyDigest },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }
}

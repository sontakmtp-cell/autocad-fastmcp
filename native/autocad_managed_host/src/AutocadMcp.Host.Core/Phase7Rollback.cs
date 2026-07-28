using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutocadMcp.Host.Core;

public static class Phase7RollbackContract
{
    public const string CheckpointVersion = "cad.rollback.checkpoint/1";
    public const string ReceiptVersion = "cad.rollback.receipt/1";
    public const int MaxEntities = 256;

    public static readonly IReadOnlySet<string> OperationIds =
        new HashSet<string>(StringComparer.Ordinal)
        {
            "cad.recovery.receipt_query",
            "cad.rollback.checkpoint.lookup",
            "cad.rollback.preview",
            "cad.rollback.commit",
            "cad.rollback.validate"
        };
}

public sealed record CadCheckpointEntity(
    string Handle,
    string EntityType,
    string Layer,
    string CanonicalFingerprint);

public sealed record CadRollbackCheckpointV1(
    string CheckpointId,
    string OriginalReceiptId,
    string OriginalReceiptDigest,
    string ProgramId,
    long ProgramRevision,
    string ProgramDigest,
    string PreviewId,
    string PreviewDigest,
    string ExecutionDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadCheckpointEntity> CreatedEntities,
    bool NonEntityObjectCreated,
    CadExecutionBinding RuntimeAndPolicyPins,
    string CreatedAt,
    string CheckpointDigest)
{
    public string SchemaVersion => Phase7RollbackContract.CheckpointVersion;

    public static string BuildCheckpointId(string originalReceiptId) =>
        $"AUTOCAD_MCP_CHECKPOINT_{HashText(originalReceiptId)[..32]}";

    public static CadRollbackCheckpointV1 Create(
        DurableProgramReceiptV02 receipt,
        CadProgramV02 program,
        CadPreviewReference preview,
        CadExecutionBinding pins,
        IReadOnlyList<CadCheckpointEntity> entities,
        bool nonEntityObjectCreated,
        DateTimeOffset createdAt)
    {
        if (entities.Count is < 1 or > Phase7RollbackContract.MaxEntities)
        {
            throw Invalid("Checkpoint entity count is outside the bounded limit.");
        }
        var checkpoint = new CadRollbackCheckpointV1(
            BuildCheckpointId(receipt.ReceiptId),
            receipt.ReceiptId,
            receipt.ReceiptDigest,
            program.ProgramId,
            program.ProgramRevision,
            program.ProgramDigest,
            preview.PreviewId,
            preview.PreviewDigest,
            pins.ExecutionDigest,
            receipt.DocumentId,
            receipt.DocumentRevisionBefore,
            receipt.DocumentRevisionAfter,
            entities,
            nonEntityObjectCreated,
            pins,
            createdAt.ToUniversalTime().ToString("O"),
            EmptyDigest);
        return checkpoint with { CheckpointDigest = checkpoint.ComputeDigest() };
    }

    public string Serialize()
    {
        Validate();
        var bytes = JsonSerializer.SerializeToUtf8Bytes(this, HostProtocol.JsonOptions);
        if (bytes.Length > 65_536)
        {
            throw Invalid("Checkpoint exceeds the bounded DWG record size.");
        }
        return Encoding.UTF8.GetString(bytes);
    }

    public static CadRollbackCheckpointV1 Parse(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json);
            RequireExactFields(
                document.RootElement,
                [
                    "checkpoint_id", "original_receipt_id", "original_receipt_digest",
                    "program_id", "program_revision", "program_digest", "preview_id",
                    "preview_digest", "execution_digest", "document_id",
                    "document_revision_before", "document_revision_after",
                    "created_entities", "non_entity_object_created",
                    "runtime_and_policy_pins", "created_at", "checkpoint_digest",
                    "schema_version"
                ]);
            if (document.RootElement.GetProperty("schema_version").GetString() !=
                Phase7RollbackContract.CheckpointVersion)
            {
                throw Invalid("Checkpoint schema version is unsupported.");
            }
            foreach (var entity in document.RootElement
                .GetProperty("created_entities")
                .EnumerateArray())
            {
                RequireExactFields(
                    entity,
                    ["handle", "entity_type", "layer", "canonical_fingerprint"]);
            }
            var value = JsonSerializer.Deserialize<CadRollbackCheckpointV1>(
                json,
                HostProtocol.JsonOptions) ?? throw Invalid("Checkpoint is empty.");
            value.Validate();
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException)
        {
            throw Invalid("Checkpoint record is malformed.");
        }
    }

    public void Validate()
    {
        RequireId(CheckpointId, 64);
        RequireId(OriginalReceiptId, 128);
        RequireId(ProgramId, 128);
        RequireId(PreviewId, 128);
        RequireId(DocumentId, 128);
        RequireDigest(OriginalReceiptDigest);
        RequireDigest(ProgramDigest);
        RequireDigest(PreviewDigest);
        RequireDigest(ExecutionDigest);
        RequireDigest(CheckpointDigest);
        if (CheckpointId != BuildCheckpointId(OriginalReceiptId) ||
            ProgramRevision < 1 ||
            CreatedEntities.Count is < 1 or > Phase7RollbackContract.MaxEntities ||
            CreatedEntities.Select(item => item.Handle).Distinct(StringComparer.Ordinal).Count()
                != CreatedEntities.Count ||
            CheckpointDigest != ComputeDigest())
        {
            throw Invalid("Checkpoint binding or digest is invalid.");
        }
        foreach (var entity in CreatedEntities)
        {
            RequireHandle(entity.Handle);
            RequireId(entity.EntityType, 128);
            RequireId(entity.Layer, 255);
            RequireDigest(entity.CanonicalFingerprint);
        }
    }

    public string ComputeDigest()
    {
        var payload = JsonSerializer.SerializeToNode(this, HostProtocol.JsonOptions)!
            .AsObject();
        payload.Remove("checkpoint_digest");
        using var document = JsonDocument.Parse(payload.ToJsonString());
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private const string EmptyDigest =
        "sha256:0000000000000000000000000000000000000000000000000000000000000000";

    private static string HashText(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))
            .ToLowerInvariant();

    internal static void RequireDigest(string value)
    {
        if (value.Length != 71 ||
            !value.StartsWith("sha256:", StringComparison.Ordinal) ||
            value[7..].Any(character => !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw Invalid("Digest is malformed.");
        }
    }

    internal static void RequireHandle(string value)
    {
        if (value.Length is < 1 or > 32 ||
            value.Any(character => !Uri.IsHexDigit(character) || char.IsLower(character)))
        {
            throw Invalid("Entity handle is malformed.");
        }
    }

    public static void RequireId(string value, int maximum)
    {
        if (string.IsNullOrWhiteSpace(value) ||
            value.Length > maximum ||
            value.Any(char.IsControl))
        {
            throw Invalid("Identifier is malformed.");
        }
    }

    internal static void RequireExactFields(
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

    internal static ProtocolValidationException Invalid(string message) =>
        new("rollback_conflict", message);
}

public sealed record CadRollbackRequest(
    string? ReceiptId,
    string? CheckpointId,
    string? CheckpointDigest,
    string? RollbackPlanId,
    string? RollbackPlanDigest,
    string? RollbackExecutionDigest,
    string? RollbackReceiptId,
    string? ExpiresAt);

public static class Phase7RollbackParser
{
    public static CadRollbackRequest Parse(string operationId, JsonElement arguments)
    {
        if (!Phase7RollbackContract.OperationIds.Contains(operationId) ||
            arguments.ValueKind != JsonValueKind.Object)
        {
            throw Invalid();
        }
        var required = operationId switch
        {
            "cad.recovery.receipt_query" => new[] { "receipt_id" },
            "cad.rollback.checkpoint.lookup" => new[] { "checkpoint_id" },
            "cad.rollback.preview" => new[]
            {
                "checkpoint_id", "checkpoint_digest", "rollback_plan_id",
                "rollback_execution_digest", "expires_at"
            },
            "cad.rollback.commit" => new[]
            {
                "checkpoint_id", "checkpoint_digest", "rollback_plan_id",
                "rollback_plan_digest", "rollback_execution_digest",
                "rollback_receipt_id", "expires_at"
            },
            "cad.rollback.validate" => new[] { "rollback_receipt_id" },
            _ => throw Invalid()
        };
        var names = arguments.EnumerateObject().Select(item => item.Name).ToArray();
        if (names.Length != required.Length ||
            names.Any(name => !required.Contains(name, StringComparer.Ordinal)))
        {
            throw Invalid();
        }
        string? Read(string name, int maximum = 128)
        {
            if (!arguments.TryGetProperty(name, out var item))
            {
                return null;
            }
            var value = item.GetString();
            CadRollbackCheckpointV1.RequireId(value ?? "", maximum);
            return value;
        }
        var result = new CadRollbackRequest(
            Read("receipt_id"),
            Read("checkpoint_id"),
            Read("checkpoint_digest", 71),
            Read("rollback_plan_id"),
            Read("rollback_plan_digest", 71),
            Read("rollback_execution_digest", 71),
            Read("rollback_receipt_id"),
            Read("expires_at", 64));
        foreach (var digest in new[]
        {
            result.CheckpointDigest,
            result.RollbackPlanDigest,
            result.RollbackExecutionDigest
        }.Where(value => value is not null))
        {
            CadRollbackCheckpointV1.RequireDigest(digest!);
        }
        if (result.ExpiresAt is not null &&
            (!DateTimeOffset.TryParse(result.ExpiresAt, out _) ||
             DateTimeOffset.Parse(result.ExpiresAt) <= DateTimeOffset.UtcNow))
        {
            throw new ProtocolValidationException(
                "rollback_plan_expired",
                "Rollback plan expiry is missing or elapsed.");
        }
        return result;
    }

    private static ProtocolValidationException Invalid() =>
        new("program_invalid", "Rollback request is not the exact typed contract.");
}

public sealed record CadRemovedEntityEvidence(
    string Handle,
    string EntityType,
    string PriorFingerprint);

public sealed record DurableRollbackReceiptV1(
    string RollbackReceiptId,
    string OriginalReceiptId,
    string OriginalReceiptDigest,
    string CheckpointId,
    string CheckpointDigest,
    string RollbackPlanId,
    string RollbackPlanDigest,
    string RollbackExecutionDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadRemovedEntityEvidence> RemovedEntities,
    CadExecutionBinding RuntimeAndPolicyPins,
    string CreatedAt,
    string ReceiptDigest)
{
    public string SchemaVersion => Phase7RollbackContract.ReceiptVersion;

    public string DictionaryKey =>
        $"AUTOCAD_MCP_ROLLBACK_{HashText(RollbackReceiptId)[..32]}";

    public static DurableRollbackReceiptV1 Create(
        string rollbackReceiptId,
        CadRollbackCheckpointV1 checkpoint,
        string rollbackPlanId,
        string rollbackPlanDigest,
        string rollbackExecutionDigest,
        string revisionBefore,
        string revisionAfter,
        IReadOnlyList<CadRemovedEntityEvidence> removed,
        DateTimeOffset createdAt)
    {
        var value = new DurableRollbackReceiptV1(
            rollbackReceiptId,
            checkpoint.OriginalReceiptId,
            checkpoint.OriginalReceiptDigest,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            rollbackPlanId,
            rollbackPlanDigest,
            rollbackExecutionDigest,
            checkpoint.DocumentId,
            revisionBefore,
            revisionAfter,
            removed,
            checkpoint.RuntimeAndPolicyPins,
            createdAt.ToUniversalTime().ToString("O"),
            EmptyDigest);
        return value with { ReceiptDigest = value.ComputeDigest() };
    }

    public string Serialize()
    {
        Validate();
        var value = JsonSerializer.Serialize(this, HostProtocol.JsonOptions);
        if (Encoding.UTF8.GetByteCount(value) > 65_536)
        {
            throw CadRollbackCheckpointV1.Invalid("Rollback receipt is too large.");
        }
        return value;
    }

    public static DurableRollbackReceiptV1 Parse(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json);
            CadRollbackCheckpointV1.RequireExactFields(
                document.RootElement,
                [
                    "rollback_receipt_id", "original_receipt_id",
                    "original_receipt_digest", "checkpoint_id", "checkpoint_digest",
                    "rollback_plan_id", "rollback_plan_digest",
                    "rollback_execution_digest", "document_id",
                    "document_revision_before", "document_revision_after",
                    "removed_entities", "runtime_and_policy_pins", "created_at",
                    "receipt_digest", "schema_version"
                ]);
            if (document.RootElement.GetProperty("schema_version").GetString() !=
                Phase7RollbackContract.ReceiptVersion)
            {
                throw CadRollbackCheckpointV1.Invalid(
                    "Rollback receipt schema version is unsupported.");
            }
            foreach (var entity in document.RootElement
                .GetProperty("removed_entities")
                .EnumerateArray())
            {
                CadRollbackCheckpointV1.RequireExactFields(
                    entity,
                    ["handle", "entity_type", "prior_fingerprint"]);
            }
            var value = JsonSerializer.Deserialize<DurableRollbackReceiptV1>(
                json,
                HostProtocol.JsonOptions)
                ?? throw CadRollbackCheckpointV1.Invalid("Rollback receipt is empty.");
            value.Validate();
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (JsonException)
        {
            throw CadRollbackCheckpointV1.Invalid("Rollback receipt is malformed.");
        }
    }

    public void Validate()
    {
        CadRollbackCheckpointV1.RequireId(RollbackReceiptId, 128);
        CadRollbackCheckpointV1.RequireId(RollbackPlanId, 128);
        CadRollbackCheckpointV1.RequireDigest(OriginalReceiptDigest);
        CadRollbackCheckpointV1.RequireDigest(CheckpointDigest);
        CadRollbackCheckpointV1.RequireDigest(RollbackPlanDigest);
        CadRollbackCheckpointV1.RequireDigest(RollbackExecutionDigest);
        CadRollbackCheckpointV1.RequireDigest(ReceiptDigest);
        if (RemovedEntities.Count is < 1 or > Phase7RollbackContract.MaxEntities ||
            RemovedEntities.Select(item => item.Handle).Distinct(StringComparer.Ordinal).Count()
                != RemovedEntities.Count ||
            ReceiptDigest != ComputeDigest())
        {
            throw CadRollbackCheckpointV1.Invalid("Rollback receipt binding is invalid.");
        }
    }

    public string ComputeDigest()
    {
        var payload = JsonSerializer.SerializeToNode(this, HostProtocol.JsonOptions)!
            .AsObject();
        payload.Remove("receipt_digest");
        using var document = JsonDocument.Parse(payload.ToJsonString());
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private const string EmptyDigest =
        "sha256:0000000000000000000000000000000000000000000000000000000000000000";

    private static string HashText(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))
            .ToLowerInvariant();
}

/// <summary>
/// Deterministic model used to verify atomic checkpoint creation and rollback.
/// Production R25 uses one AutoCAD transaction for the equivalent transition.
/// </summary>
public sealed class Phase7RollbackTransactionModel
{
    private readonly Dictionary<string, string> _entities = new(StringComparer.Ordinal);
    private readonly Dictionary<string, CadRollbackCheckpointV1> _checkpoints =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, DurableRollbackReceiptV1> _receipts =
        new(StringComparer.Ordinal);

    public IReadOnlyDictionary<string, string> Entities => _entities;
    public IReadOnlyDictionary<string, CadRollbackCheckpointV1> Checkpoints => _checkpoints;
    public IReadOnlyDictionary<string, DurableRollbackReceiptV1> Receipts => _receipts;

    public void SimulateEntityDrift(string handle, string fingerprint) =>
        _entities[handle] = fingerprint;

    public void Commit(
        CadRollbackCheckpointV1 checkpoint,
        bool failBeforeCommit = false)
    {
        var entities = new Dictionary<string, string>(_entities, StringComparer.Ordinal);
        var checkpoints = new Dictionary<string, CadRollbackCheckpointV1>(
            _checkpoints,
            StringComparer.Ordinal);
        foreach (var entity in checkpoint.CreatedEntities)
        {
            entities.Add(entity.Handle, entity.CanonicalFingerprint);
        }
        checkpoints.Add(checkpoint.CheckpointId, checkpoint);
        if (failBeforeCommit)
        {
            throw new InvalidOperationException("Injected transaction failure.");
        }
        Replace(_entities, entities);
        Replace(_checkpoints, checkpoints);
    }

    public DurableRollbackReceiptV1 Rollback(
        DurableRollbackReceiptV1 receipt,
        bool failBeforeCommit = false)
    {
        if (_receipts.TryGetValue(receipt.RollbackReceiptId, out var existing))
        {
            if (existing.ReceiptDigest != receipt.ReceiptDigest)
            {
                throw new ProtocolValidationException(
                    "duplicate_payload_mismatch",
                    "Rollback idempotency key was reused with another payload.");
            }
            return existing;
        }
        if (!_checkpoints.TryGetValue(receipt.CheckpointId, out var checkpoint) ||
            checkpoint.CheckpointDigest != receipt.CheckpointDigest)
        {
            throw CadRollbackCheckpointV1.Invalid("Checkpoint evidence is unavailable.");
        }
        var entities = new Dictionary<string, string>(_entities, StringComparer.Ordinal);
        foreach (var entity in checkpoint.CreatedEntities)
        {
            if (!entities.TryGetValue(entity.Handle, out var fingerprint) ||
                fingerprint != entity.CanonicalFingerprint)
            {
                throw CadRollbackCheckpointV1.Invalid("Entity drift blocks rollback.");
            }
            entities.Remove(entity.Handle);
        }
        var receipts = new Dictionary<string, DurableRollbackReceiptV1>(
            _receipts,
            StringComparer.Ordinal)
        {
            [receipt.RollbackReceiptId] = receipt
        };
        if (failBeforeCommit)
        {
            throw new InvalidOperationException("Injected rollback transaction failure.");
        }
        Replace(_entities, entities);
        Replace(_receipts, receipts);
        return receipt;
    }

    private static void Replace<T>(Dictionary<string, T> target, Dictionary<string, T> source)
    {
        target.Clear();
        foreach (var item in source)
        {
            target.Add(item.Key, item.Value);
        }
    }
}

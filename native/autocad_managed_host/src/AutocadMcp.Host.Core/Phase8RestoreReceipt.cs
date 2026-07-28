using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AutocadMcp.Host.Core;

public sealed record CadManagedRestoreReceipt(
    string SchemaVersion,
    string RestoreReceiptId,
    string RestoreIdentityDigest,
    string CheckpointId,
    string CheckpointDigest,
    string PlanDigest,
    string EffectDigest,
    string TargetSetDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadManagedModifiedEntity> RestoredEntities,
    CadManagedRuntimePins Pins,
    string CreatedAt,
    string ReceiptDigest)
{
    public const string Version = "cad.restore.receipt/2";

    public static CadManagedRestoreReceipt Create(
        CadRollbackCheckpointV2 checkpoint,
        string documentRevisionAfter,
        IReadOnlyList<CadManagedModifiedEntity> restoredEntities,
        DateTimeOffset createdAt)
    {
        var identityDigest = BuildIdentityDigest(checkpoint);
        var value = new CadManagedRestoreReceipt(
            Version,
            BuildReceiptId(checkpoint),
            identityDigest,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            checkpoint.PlanDigest,
            checkpoint.EffectDigest,
            checkpoint.TargetSetDigest,
            checkpoint.DocumentId,
            checkpoint.DocumentRevisionAfter,
            documentRevisionAfter,
            restoredEntities,
            checkpoint.RuntimePins,
            createdAt.ToUniversalTime().ToString("O"),
            $"sha256:{new string('0', 64)}");
        value.Validate(includeDigest: false);
        return value with { ReceiptDigest = value.ComputeDigest() };
    }

    public static CadManagedRestoreReceipt Parse(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 12
            });
            var fields = new HashSet<string>(StringComparer.Ordinal)
            {
                "schema_version", "restore_receipt_id",
                "restore_identity_digest", "checkpoint_id",
                "checkpoint_digest", "plan_digest", "effect_digest",
                "target_set_digest", "document_id",
                "document_revision_before", "document_revision_after",
                "restored_entities", "pins", "created_at", "receipt_digest"
            };
            if (document.RootElement.ValueKind != JsonValueKind.Object ||
                document.RootElement.EnumerateObject().Count() != fields.Count ||
                document.RootElement.EnumerateObject()
                    .Any(property => !fields.Contains(property.Name)))
            {
                throw Invalid();
            }
            var value = JsonSerializer.Deserialize<CadManagedRestoreReceipt>(
                document.RootElement.GetRawText(),
                HostProtocol.JsonOptions) ?? throw Invalid();
            value.Validate(includeDigest: true);
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException or FormatException)
        {
            throw Invalid();
        }
    }

    public string Serialize()
    {
        Validate(includeDigest: true);
        return JsonSerializer.Serialize(this, HostProtocol.JsonOptions);
    }

    public static string BuildIdentityDigest(CadRollbackCheckpointV2 checkpoint)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                domain = "cad.restore-effect-identity/1",
                action = "restore",
                checkpoint.CheckpointId,
                checkpoint.CheckpointDigest,
                checkpoint.PlanDigest,
                checkpoint.EffectDigest,
                checkpoint.TargetSetDigest,
                checkpoint.DocumentId,
                document_revision_before = checkpoint.DocumentRevisionAfter,
                checkpoint.RuntimePins
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    public static string BuildReceiptId(CadRollbackCheckpointV2 checkpoint)
    {
        var idHash = Convert.ToHexString(SHA256.HashData(
            Encoding.UTF8.GetBytes(BuildIdentityDigest(checkpoint)))).ToLowerInvariant();
        return $"AUTOCAD_MCP_RESTORE_V2_{idHash[..32]}";
    }

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Version ||
            RestoredEntities.Count is < 1 or > Phase8ManagedOperationContract.MaxOperations ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw Invalid();
        }
        Phase8ManagedOperationRegistry.RequireIdentifier(RestoreReceiptId, 64);
        Phase8ManagedOperationRegistry.RequireIdentifier(CheckpointId, 64);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(RestoreIdentityDigest);
        Phase8ManagedOperationRegistry.RequireDigest(CheckpointDigest);
        Phase8ManagedOperationRegistry.RequireDigest(PlanDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectDigest);
        Phase8ManagedOperationRegistry.RequireDigest(TargetSetDigest);
        CadManagedRuntimePinValidation.Validate(Pins);
        foreach (var entity in RestoredEntities)
        {
            Phase8ManagedOperationRegistry.RequireIdentifier(entity.OperationId, 128);
            Phase8ManagedOperationRegistry.RequireIdentifier(entity.EntityId, 128);
            Phase8ManagedOperationRegistry.RequireDigest(entity.FingerprintBefore);
            Phase8ManagedOperationRegistry.RequireDigest(entity.FingerprintAfter);
        }
        if (includeDigest)
        {
            Phase8ManagedOperationRegistry.RequireDigest(ReceiptDigest);
            if (ReceiptDigest != ComputeDigest())
            {
                throw Invalid();
            }
        }
    }

    private string ComputeDigest()
    {
        using var document = JsonSerializer.SerializeToDocument(
            this with { ReceiptDigest = $"sha256:{new string('0', 64)}" },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static ProtocolValidationException Invalid() =>
        new("ledger_corrupt", "Transform restore receipt is invalid.");
}

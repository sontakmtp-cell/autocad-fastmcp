using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AutocadMcp.Host.Core;

public sealed record CadRemovedOutputV1(
    string OutputId,
    string EntityId,
    string EntityType,
    string Fingerprint);

public sealed record CadCreatedOutputRollbackReceiptV1(
    string SchemaVersion,
    string RollbackReceiptId,
    string RollbackIdentityDigest,
    string CheckpointId,
    string CheckpointDigest,
    string EffectIdentityDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadRemovedOutputV1> RemovedOutputs,
    CadManagedRuntimePins Pins,
    string CreatedAt,
    string ReceiptDigest)
{
    public const string Version = "cad.created-output.rollback-receipt/1";

    public static CadCreatedOutputRollbackReceiptV1 Create(
        CadCreatedOutputCheckpointV1 checkpoint,
        string documentRevisionAfter,
        IReadOnlyList<CadRemovedOutputV1> removedOutputs,
        DateTimeOffset createdAt)
    {
        var identity = BuildIdentityDigest(checkpoint);
        var value = new CadCreatedOutputRollbackReceiptV1(
            Version,
            BuildReceiptId(checkpoint),
            identity,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            checkpoint.EffectIdentityDigest,
            checkpoint.DocumentId,
            checkpoint.DocumentRevisionAfter,
            documentRevisionAfter,
            removedOutputs,
            checkpoint.Pins,
            createdAt.ToUniversalTime().ToString("O"),
            $"sha256:{new string('0', 64)}");
        value.Validate(includeDigest: false);
        return value with { ReceiptDigest = value.ComputeDigest() };
    }

    public static CadCreatedOutputRollbackReceiptV1 Parse(string json)
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
                "schema_version", "rollback_receipt_id",
                "rollback_identity_digest", "checkpoint_id",
                "checkpoint_digest", "effect_identity_digest", "document_id",
                "document_revision_before", "document_revision_after",
                "removed_outputs", "pins", "created_at", "receipt_digest"
            };
            if (document.RootElement.ValueKind != JsonValueKind.Object ||
                document.RootElement.EnumerateObject().Count() != fields.Count ||
                document.RootElement.EnumerateObject()
                    .Any(property => !fields.Contains(property.Name)))
            {
                throw Invalid();
            }
            var value = JsonSerializer.Deserialize<CadCreatedOutputRollbackReceiptV1>(
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

    public static string BuildIdentityDigest(
        CadCreatedOutputCheckpointV1 checkpoint)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                domain = "cad.created-output-rollback-identity/1",
                action = "erase_created_outputs",
                checkpoint.CheckpointId,
                checkpoint.CheckpointDigest,
                checkpoint.EffectIdentityDigest,
                checkpoint.TargetSetDigest,
                checkpoint.DocumentId,
                document_revision_before = checkpoint.DocumentRevisionAfter,
                checkpoint.Pins
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    public static string BuildReceiptId(CadCreatedOutputCheckpointV1 checkpoint)
    {
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            BuildIdentityDigest(checkpoint)))).ToLowerInvariant();
        return $"AUTOCAD_MCP_CREATED_RB_{hash[..32]}";
    }

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Version ||
            RemovedOutputs.Count is < 1 or > Phase8ManagedOperationContract.MaxOutputs ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw Invalid();
        }
        Phase8ManagedOperationRegistry.RequireIdentifier(RollbackReceiptId, 64);
        Phase8ManagedOperationRegistry.RequireIdentifier(CheckpointId, 64);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(RollbackIdentityDigest);
        Phase8ManagedOperationRegistry.RequireDigest(CheckpointDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectIdentityDigest);
        CadManagedRuntimePinValidation.Validate(Pins);
        foreach (var output in RemovedOutputs)
        {
            Phase8ManagedOperationRegistry.RequireIdentifier(output.OutputId, 128);
            Phase8ManagedOperationRegistry.RequireIdentifier(output.EntityId, 128);
            Phase8ManagedOperationRegistry.RequireDigest(output.Fingerprint);
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
        new("ledger_corrupt", "Created-output rollback receipt is invalid.");
}

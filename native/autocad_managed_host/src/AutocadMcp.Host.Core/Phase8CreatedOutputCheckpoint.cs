using System.Text.Json;

namespace AutocadMcp.Host.Core;

public sealed record CadCreatedOutputCheckpointV1(
    string SchemaVersion,
    string CheckpointId,
    string ReceiptId,
    string PlanDigest,
    string EffectDigest,
    string EffectIdentityDigest,
    string TargetSetDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadManagedOutputMapping> CreatedOutputs,
    CadManagedRuntimePins Pins,
    string CreatedAt,
    string CheckpointDigest)
{
    public const string Version = "cad.created-output.checkpoint/1";

    public static CadCreatedOutputCheckpointV1 Create(
        CadManagedSealedPlan plan,
        CadManagedEffectIdentity identity,
        string documentRevisionAfter,
        IReadOnlyList<CadManagedOutputMapping> outputs,
        DateTimeOffset createdAt)
    {
        plan.Validate();
        if (outputs.Count is < 1 or > Phase8ManagedOperationContract.MaxOutputs)
        {
            throw Invalid();
        }
        var value = new CadCreatedOutputCheckpointV1(
            Version,
            CadRollbackCheckpointV1.BuildCheckpointId(identity.ReceiptId),
            identity.ReceiptId,
            plan.PlanDigest,
            plan.EffectDigest,
            identity.EffectIdentityDigest,
            plan.TargetSetDigest,
            plan.DocumentId,
            plan.DocumentRevision,
            documentRevisionAfter,
            outputs,
            plan.Pins,
            createdAt.ToUniversalTime().ToString("O"),
            $"sha256:{new string('0', 64)}");
        value.Validate(includeDigest: false);
        return value with { CheckpointDigest = value.ComputeDigest() };
    }

    public static CadCreatedOutputCheckpointV1 Parse(string json)
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
                "schema_version", "checkpoint_id", "receipt_id", "plan_digest",
                "effect_digest", "effect_identity_digest", "target_set_digest",
                "document_id", "document_revision_before",
                "document_revision_after", "created_outputs", "pins",
                "created_at", "checkpoint_digest"
            };
            if (document.RootElement.ValueKind != JsonValueKind.Object ||
                document.RootElement.EnumerateObject().Count() != fields.Count ||
                document.RootElement.EnumerateObject()
                    .Any(property => !fields.Contains(property.Name)))
            {
                throw Invalid();
            }
            var value = JsonSerializer.Deserialize<CadCreatedOutputCheckpointV1>(
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

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Version ||
            CheckpointId != CadRollbackCheckpointV1.BuildCheckpointId(ReceiptId) ||
            CreatedOutputs.Count is < 1 or > Phase8ManagedOperationContract.MaxOutputs ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw Invalid();
        }
        Phase8ManagedOperationRegistry.RequireIdentifier(ReceiptId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(PlanDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectIdentityDigest);
        Phase8ManagedOperationRegistry.RequireDigest(TargetSetDigest);
        CadManagedRuntimePinValidation.Validate(Pins);
        var outputIds = new HashSet<string>(StringComparer.Ordinal);
        var entityIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var output in CreatedOutputs)
        {
            Phase8ManagedOperationRegistry.RequireIdentifier(output.OperationId, 128);
            Phase8ManagedOperationRegistry.RequireIdentifier(output.OutputId, 128);
            Phase8ManagedOperationRegistry.RequireIdentifier(output.EntityId, 128);
            Phase8ManagedOperationRegistry.RequireDigest(output.Fingerprint);
            if (!Phase8ManagedOperationContract.SupportedEntityTypes.Contains(
                    output.EntityType) ||
                !outputIds.Add(output.OutputId) ||
                !entityIds.Add(output.EntityId))
            {
                throw Invalid();
            }
        }
        if (includeDigest)
        {
            Phase8ManagedOperationRegistry.RequireDigest(CheckpointDigest);
            if (CheckpointDigest != ComputeDigest())
            {
                throw Invalid();
            }
        }
    }

    private string ComputeDigest()
    {
        using var document = JsonSerializer.SerializeToDocument(
            this with { CheckpointDigest = $"sha256:{new string('0', 64)}" },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static ProtocolValidationException Invalid() =>
        new("rollback_conflict", "Created-output checkpoint v1 is invalid.");
}

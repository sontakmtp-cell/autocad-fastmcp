using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AutocadMcp.Host.Core;

public static class Phase8ManagedOperationContract
{
    public const string RegistryVersion = "cad.operation-registry/1";
    public const string CheckpointV2Version = "cad.rollback.checkpoint/2";
    public const string ReceiptVersion = "cad.operation.receipt/1";
    public const int MaxOperations = 256;
    public const int MaxOutputs = 256;
    public const int MaxRestoreBytes = 64 * 1024;
    public const int MaxPolylineVertices = 4096;
    public const double MaxCoordinateMagnitude = 1_000_000_000d;

    public static readonly IReadOnlySet<string> SupportedEntityTypes =
        new HashSet<string>(StringComparer.Ordinal)
        {
            "LINE",
            "CIRCLE",
            "LWPOLYLINE"
        };
}

public enum CadOperationEffectClass
{
    CreateEquivalent,
    ExactTransform
}

public enum CadCheckpointStrategy
{
    CreatedOutputsV1,
    RestorePreImageV2
}

public sealed record CadManagedOperationDescriptor(
    string Kind,
    int Version,
    CadOperationEffectClass EffectClass,
    CadCheckpointStrategy CheckpointStrategy,
    string CapabilityKey,
    IReadOnlySet<string> EntityTypes);

public static class Phase8ManagedOperationRegistry
{
    private static readonly IReadOnlyDictionary<string, CadManagedOperationDescriptor> Entries =
        new Dictionary<string, CadManagedOperationDescriptor>(StringComparer.Ordinal)
        {
            ["copy_entity"] = Create(
                "copy_entity",
                CadOperationEffectClass.CreateEquivalent,
                CadCheckpointStrategy.CreatedOutputsV1,
                "cad.op.copy"),
            ["pattern_linear"] = Create(
                "pattern_linear",
                CadOperationEffectClass.CreateEquivalent,
                CadCheckpointStrategy.CreatedOutputsV1,
                "cad.op.pattern.linear"),
            ["pattern_rectangular"] = Create(
                "pattern_rectangular",
                CadOperationEffectClass.CreateEquivalent,
                CadCheckpointStrategy.CreatedOutputsV1,
                "cad.op.pattern.rectangular"),
            ["pattern_polar"] = Create(
                "pattern_polar",
                CadOperationEffectClass.CreateEquivalent,
                CadCheckpointStrategy.CreatedOutputsV1,
                "cad.op.pattern.polar"),
            ["offset_entity"] = Create(
                "offset_entity",
                CadOperationEffectClass.CreateEquivalent,
                CadCheckpointStrategy.CreatedOutputsV1,
                "cad.op.offset"),
            ["move_entity"] = Create(
                "move_entity",
                CadOperationEffectClass.ExactTransform,
                CadCheckpointStrategy.RestorePreImageV2,
                "cad.op.move"),
            ["rotate_entity"] = Create(
                "rotate_entity",
                CadOperationEffectClass.ExactTransform,
                CadCheckpointStrategy.RestorePreImageV2,
                "cad.op.rotate"),
            ["scale_entity"] = Create(
                "scale_entity",
                CadOperationEffectClass.ExactTransform,
                CadCheckpointStrategy.RestorePreImageV2,
                "cad.op.scale")
        };

    public static IReadOnlyCollection<CadManagedOperationDescriptor> Operations { get; } =
        Entries.Values.ToArray();
    public static string RegistryDigest { get; } = ComputeRegistryDigest();

    public static CadManagedOperationDescriptor Require(string kind, string entityType)
    {
        if (!Entries.TryGetValue(kind, out var descriptor) ||
            !descriptor.EntityTypes.Contains(entityType))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Managed R25 operation or entity type is not in the Phase 8 allowlist.");
        }
        return descriptor;
    }

    public static void ValidateBatch(IReadOnlyList<CadManagedOperation> operations)
    {
        if (operations.Count is < 1 or > Phase8ManagedOperationContract.MaxOperations)
        {
            throw Invalid("Managed operation count is outside the bounded limit.");
        }

        var operationIds = new HashSet<string>(StringComparer.Ordinal);
        var outputIds = new HashSet<string>(StringComparer.Ordinal);
        var modifiedEntityIds = new HashSet<string>(StringComparer.Ordinal);
        var outputCount = 0;
        foreach (var operation in operations)
        {
            RequireIdentifier(operation.OperationId, 128);
            if (!operationIds.Add(operation.OperationId))
            {
                throw Invalid("Managed operation IDs must be unique.");
            }
            operation.Validate();
            var descriptor = Require(operation.Kind, operation.Target.EntityType);
            if (descriptor.EffectClass == CadOperationEffectClass.ExactTransform &&
                !modifiedEntityIds.Add(operation.Target.EntityId))
            {
                throw Invalid(
                    "A target entity may be modified only once in a sealed operation batch.");
            }
            foreach (var outputId in operation.OutputIds)
            {
                RequireIdentifier(outputId, 128);
                if (!outputIds.Add(outputId))
                {
                    throw Invalid("Managed operation output IDs must be unique.");
                }
                outputCount++;
            }
        }
        if (outputCount > Phase8ManagedOperationContract.MaxOutputs)
        {
            throw Invalid("Managed operation output count exceeds the bounded limit.");
        }
    }

    internal static void RequireIdentifier(string value, int maximum)
    {
        if (string.IsNullOrEmpty(value) ||
            value.Length > maximum ||
            value.Any(character =>
                !(char.IsAsciiLetterOrDigit(character) ||
                  character is '.' or '_' or '-')))
        {
            throw Invalid("Managed operation identifier is invalid.");
        }
    }

    internal static void RequireDigest(string value)
    {
        if (value.Length != 71 ||
            !value.StartsWith("sha256:", StringComparison.Ordinal) ||
            value[7..].Any(character =>
                !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw Invalid("Managed operation digest is invalid.");
        }
    }

    internal static void RequireFinite(double value, bool positive = false)
    {
        if (!double.IsFinite(value) ||
            Math.Abs(value) > Phase8ManagedOperationContract.MaxCoordinateMagnitude ||
            (positive && value <= 0))
        {
            throw Invalid("Managed operation number is outside the bounded limit.");
        }
    }

    internal static ProtocolValidationException Invalid(string message) =>
        new("program_invalid", message);

    private static CadManagedOperationDescriptor Create(
        string kind,
        CadOperationEffectClass effectClass,
        CadCheckpointStrategy checkpointStrategy,
        string capabilityPrefix) =>
        new(
            kind,
            1,
            effectClass,
            checkpointStrategy,
            $"{capabilityPrefix}.line-circle-lwpolyline.v1",
            Phase8ManagedOperationContract.SupportedEntityTypes);

    private static string ComputeRegistryDigest()
    {
        var projection = Entries.Values
            .OrderBy(entry => entry.Kind, StringComparer.Ordinal)
            .Select(entry => new
            {
                entry.Kind,
                entry.Version,
                effect_class = entry.EffectClass.ToString(),
                checkpoint_strategy = entry.CheckpointStrategy.ToString(),
                entry.CapabilityKey,
                entity_types = entry.EntityTypes.Order(StringComparer.Ordinal).ToArray()
            }).ToArray();
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                registry_version = Phase8ManagedOperationContract.RegistryVersion,
                operations = projection
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }
}

public sealed record CadManagedPoint(double X, double Y, double Z)
{
    public void Validate()
    {
        Phase8ManagedOperationRegistry.RequireFinite(X);
        Phase8ManagedOperationRegistry.RequireFinite(Y);
        Phase8ManagedOperationRegistry.RequireFinite(Z);
    }
}

public sealed record CadManagedVector(double X, double Y, double Z)
{
    public void Validate(bool allowZero = false)
    {
        Phase8ManagedOperationRegistry.RequireFinite(X);
        Phase8ManagedOperationRegistry.RequireFinite(Y);
        Phase8ManagedOperationRegistry.RequireFinite(Z);
        if (!allowZero && X == 0 && Y == 0 && Z == 0)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Managed operation vector must not be zero.");
        }
    }
}

public sealed record CadStableEntityRef(
    string DocumentId,
    string EntityId,
    string EntityType,
    string Fingerprint)
{
    public void Validate()
    {
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(EntityId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(Fingerprint);
        if (!Phase8ManagedOperationContract.SupportedEntityTypes.Contains(EntityType))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Entity type has no Managed R25 Phase 8 support.");
        }
    }
}

public abstract record CadManagedOperation(
    string Kind,
    string OperationId,
    CadStableEntityRef Target)
{
    public abstract IReadOnlyList<string> OutputIds { get; }

    public virtual void Validate() => Target.Validate();
}

public sealed record CadCopyEntityOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedVector Displacement,
    string OutputId) : CadManagedOperation("copy_entity", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => [OutputId];

    public override void Validate()
    {
        base.Validate();
        Displacement.Validate();
    }
}

public sealed record CadLinearPatternOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedVector Step,
    int Count,
    IReadOnlyList<string> StableOutputIds)
    : CadManagedOperation("pattern_linear", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => StableOutputIds;

    public override void Validate()
    {
        base.Validate();
        Step.Validate();
        if (Count is < 2 or > Phase8ManagedOperationContract.MaxOutputs ||
            StableOutputIds.Count != Count - 1)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Linear pattern count and output mapping disagree.");
        }
    }
}

public sealed record CadRectangularPatternOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedVector ColumnStep,
    int ColumnCount,
    CadManagedVector RowStep,
    int RowCount,
    IReadOnlyList<string> StableOutputIds)
    : CadManagedOperation("pattern_rectangular", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => StableOutputIds;

    public override void Validate()
    {
        base.Validate();
        ColumnStep.Validate();
        RowStep.Validate();
        var outputs = checked((long)ColumnCount * RowCount - 1);
        if (ColumnCount is < 1 or > Phase8ManagedOperationContract.MaxOutputs ||
            RowCount is < 1 or > Phase8ManagedOperationContract.MaxOutputs ||
            outputs is < 1 or > Phase8ManagedOperationContract.MaxOutputs ||
            StableOutputIds.Count != outputs)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Rectangular pattern dimensions and output mapping disagree.");
        }
    }
}

public sealed record CadPolarPatternOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedPoint Center,
    int Count,
    double TotalAngleRadians,
    bool RotateItems,
    IReadOnlyList<string> StableOutputIds)
    : CadManagedOperation("pattern_polar", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => StableOutputIds;

    public override void Validate()
    {
        base.Validate();
        Center.Validate();
        Phase8ManagedOperationRegistry.RequireFinite(TotalAngleRadians);
        if (Count is < 2 or > Phase8ManagedOperationContract.MaxOutputs ||
            TotalAngleRadians == 0 ||
            Math.Abs(TotalAngleRadians) > Math.PI * 2 ||
            !RotateItems ||
            StableOutputIds.Count != Count - 1)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Polar pattern parameters are unsafe or output mapping disagrees.");
        }
    }
}

public sealed record CadOffsetEntityOperation(
    string OperationId,
    CadStableEntityRef Target,
    double SignedDistance,
    string OutputId)
    : CadManagedOperation("offset_entity", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => [OutputId];

    public override void Validate()
    {
        base.Validate();
        Phase8ManagedOperationRegistry.RequireFinite(SignedDistance);
        if (SignedDistance == 0)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Offset distance must not be zero.");
        }
    }
}

public sealed record CadMoveEntityOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedVector Displacement)
    : CadManagedOperation("move_entity", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => [];

    public override void Validate()
    {
        base.Validate();
        Displacement.Validate();
    }
}

public sealed record CadRotateEntityOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedPoint BasePoint,
    double AngleRadians)
    : CadManagedOperation("rotate_entity", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => [];

    public override void Validate()
    {
        base.Validate();
        BasePoint.Validate();
        Phase8ManagedOperationRegistry.RequireFinite(AngleRadians);
        if (AngleRadians == 0 || Math.Abs(AngleRadians) > Math.PI * 2)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Rotation angle is outside the exact transform range.");
        }
    }
}

public sealed record CadScaleEntityOperation(
    string OperationId,
    CadStableEntityRef Target,
    CadManagedPoint BasePoint,
    double UniformFactor)
    : CadManagedOperation("scale_entity", OperationId, Target)
{
    public override IReadOnlyList<string> OutputIds => [];

    public override void Validate()
    {
        base.Validate();
        BasePoint.Validate();
        Phase8ManagedOperationRegistry.RequireFinite(UniformFactor, positive: true);
        if (UniformFactor == 1)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Scale factor must change the target.");
        }
    }
}

public sealed record CadEntityStyleV2(
    string Layer,
    string Linetype,
    double LinetypeScale,
    int LineWeight,
    bool Visible,
    short ColorIndex);

public sealed record CadLwPolylineVertexV2(double X, double Y, double Bulge);

public sealed record CadEntityRestoreDescriptorV2(
    string DescriptorSchema,
    string RestoreStrategy,
    string EntityType,
    CadEntityStyleV2 Style,
    CadManagedPoint? LineStart,
    CadManagedPoint? LineEnd,
    CadManagedPoint? CircleCenter,
    CadManagedVector? CircleNormal,
    double? CircleRadius,
    double? PolylineElevation,
    bool? PolylineClosed,
    IReadOnlyList<CadLwPolylineVertexV2>? PolylineVertices)
{
    public void Validate()
    {
        if (DescriptorSchema != "cad.restore-descriptor/2" ||
            RestoreStrategy != "restore_allowlisted_preimage")
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Restore descriptor schema or strategy is unsupported.");
        }
        if (!Phase8ManagedOperationContract.SupportedEntityTypes.Contains(EntityType))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Restore descriptor entity type is unsupported.");
        }
        if (string.IsNullOrWhiteSpace(Style.Layer) ||
            Style.Layer.Length > 255 ||
            string.IsNullOrWhiteSpace(Style.Linetype) ||
            Style.Linetype.Length > 255)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Restore descriptor style is invalid.");
        }
        Phase8ManagedOperationRegistry.RequireFinite(Style.LinetypeScale, positive: true);

        switch (EntityType)
        {
            case "LINE" when LineStart is not null && LineEnd is not null &&
                             CircleCenter is null && CircleNormal is null &&
                             CircleRadius is null && PolylineElevation is null &&
                             PolylineClosed is null && PolylineVertices is null:
                LineStart.Validate();
                LineEnd.Validate();
                if (LineStart == LineEnd)
                {
                    throw Phase8ManagedOperationRegistry.Invalid(
                        "Restore line endpoints must differ.");
                }
                break;
            case "CIRCLE" when CircleCenter is not null && CircleNormal is not null &&
                               CircleRadius is not null && LineStart is null &&
                               LineEnd is null && PolylineElevation is null &&
                               PolylineClosed is null && PolylineVertices is null:
                CircleCenter.Validate();
                CircleNormal.Validate();
                Phase8ManagedOperationRegistry.RequireFinite(
                    CircleRadius.Value,
                    positive: true);
                break;
            case "LWPOLYLINE" when PolylineElevation is not null &&
                                   PolylineClosed is not null &&
                                   PolylineVertices is not null &&
                                   LineStart is null && LineEnd is null &&
                                   CircleCenter is null && CircleNormal is null &&
                                   CircleRadius is null:
                Phase8ManagedOperationRegistry.RequireFinite(PolylineElevation.Value);
                if (PolylineVertices.Count is < 2 or >
                    Phase8ManagedOperationContract.MaxPolylineVertices)
                {
                    throw Phase8ManagedOperationRegistry.Invalid(
                        "Restore polyline vertex count is outside the bounded limit.");
                }
                foreach (var vertex in PolylineVertices)
                {
                    Phase8ManagedOperationRegistry.RequireFinite(vertex.X);
                    Phase8ManagedOperationRegistry.RequireFinite(vertex.Y);
                    Phase8ManagedOperationRegistry.RequireFinite(vertex.Bulge);
                }
                break;
            default:
                throw Phase8ManagedOperationRegistry.Invalid(
                    "Restore descriptor geometry does not match its entity type.");
        }
    }
}

public sealed record CadDependencyRefV2(
    string Kind,
    string Name,
    string Fingerprint);

public sealed record CadRestoreEntryV2(
    string OperationId,
    CadStableEntityRef TargetBefore,
    string FingerprintAfter,
    string Space,
    string SnapshotId,
    IReadOnlyList<CadDependencyRefV2> Dependencies,
    string DependencyClosureDigest,
    CadEntityRestoreDescriptorV2 RestoreDescriptor,
    string DescriptorDigest);

public static class CadRestoreEvidenceDigest
{
    public static string Dependencies(IReadOnlyList<CadDependencyRefV2> dependencies) =>
        Hash("cad.dependency-closure/1", dependencies);

    public static string Descriptor(CadEntityRestoreDescriptorV2 descriptor) =>
        Hash("cad.restore-descriptor/2", descriptor);

    private static string Hash(string domain, object value)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new { domain, value },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }
}

public sealed record CadManagedRuntimePins(
    string RuntimeId,
    string HostFamily,
    string HostVersion,
    string PackageHash,
    string OperationRegistryVersion,
    string OperationRegistryHash,
    string CompilerHash,
    string CapabilityManifestHash,
    string CapabilityEvidenceHash,
    string PolicyDigest,
    string RolloutPolicyDigest);

public sealed record CadRollbackCheckpointV2(
    string SchemaVersion,
    string CheckpointId,
    string ReceiptId,
    string PlanDigest,
    string EffectDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadRestoreEntryV2> RestoreEntries,
    CadManagedRuntimePins RuntimePins,
    string OwnerId,
    string DeviceId,
    string SnapshotId,
    string TargetSetDigest,
    int RestoreBudgetBytes,
    string CreatedAt,
    string CheckpointDigest)
{
    private static readonly IReadOnlySet<string> ExactFields =
        new HashSet<string>(StringComparer.Ordinal)
        {
            "schema_version",
            "checkpoint_id",
            "receipt_id",
            "plan_digest",
            "effect_digest",
            "document_id",
            "document_revision_before",
            "document_revision_after",
            "restore_entries",
            "runtime_pins",
            "owner_id",
            "device_id",
            "snapshot_id",
            "target_set_digest",
            "restore_budget_bytes",
            "created_at",
            "checkpoint_digest"
        };

    public static CadRollbackCheckpointV2 Create(
        string receiptId,
        string planDigest,
        string effectDigest,
        string documentId,
        string documentRevisionBefore,
        string documentRevisionAfter,
        IReadOnlyList<CadRestoreEntryV2> restoreEntries,
        CadManagedRuntimePins runtimePins,
        string ownerId,
        string deviceId,
        string snapshotId,
        string targetSetDigest,
        DateTimeOffset createdAt)
    {
        var checkpoint = new CadRollbackCheckpointV2(
            Phase8ManagedOperationContract.CheckpointV2Version,
            BuildCheckpointId(receiptId),
            receiptId,
            planDigest,
            effectDigest,
            documentId,
            documentRevisionBefore,
            documentRevisionAfter,
            restoreEntries,
            runtimePins,
            ownerId,
            deviceId,
            snapshotId,
            targetSetDigest,
            0,
            createdAt.ToUniversalTime().ToString("O"),
            $"sha256:{new string('0', 64)}");
        checkpoint.Validate(includeDigest: false);
        var budget = Encoding.UTF8.GetByteCount(checkpoint.SerializeCore(includeDigest: false));
        if (budget > Phase8ManagedOperationContract.MaxRestoreBytes)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Checkpoint v2 restore payload exceeds the bounded limit.");
        }
        checkpoint = checkpoint with { RestoreBudgetBytes = budget };
        return checkpoint with { CheckpointDigest = checkpoint.ComputeDigest() };
    }

    public static CadRollbackCheckpointV2 Parse(string json)
    {
        if (Encoding.UTF8.GetByteCount(json) >
            Phase8ManagedOperationContract.MaxRestoreBytes + 8192)
        {
            throw InvalidCheckpoint("Checkpoint v2 record is too large.");
        }
        try
        {
            using var document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 16
            });
            RequireExact(document.RootElement, ExactFields);
            ValidateRestoreShape(document.RootElement);
            var checkpoint = JsonSerializer.Deserialize<CadRollbackCheckpointV2>(
                document.RootElement.GetRawText(),
                HostProtocol.JsonOptions)
                ?? throw InvalidCheckpoint("Checkpoint v2 record is empty.");
            checkpoint.Validate(includeDigest: true);
            return checkpoint;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException or FormatException)
        {
            throw InvalidCheckpoint("Checkpoint v2 record is malformed.");
        }
    }

    public string Serialize()
    {
        Validate(includeDigest: true);
        return SerializeCore(includeDigest: true);
    }

    public string ComputeDigest()
    {
        using var document = JsonDocument.Parse(SerializeCore(includeDigest: false));
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    public static string BuildCheckpointId(string receiptId)
    {
        Phase8ManagedOperationRegistry.RequireIdentifier(receiptId, 128);
        var hash = Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(receiptId))).ToLowerInvariant();
        return $"AUTOCAD_MCP_CHECKPOINT_V2_{hash[..32]}";
    }

    private void Validate(bool includeDigest)
    {
        if (SchemaVersion != Phase8ManagedOperationContract.CheckpointV2Version)
        {
            throw InvalidCheckpoint("Checkpoint v2 schema version is unsupported.");
        }
        Phase8ManagedOperationRegistry.RequireIdentifier(CheckpointId, 64);
        Phase8ManagedOperationRegistry.RequireIdentifier(ReceiptId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(OwnerId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DeviceId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(SnapshotId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(PlanDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectDigest);
        Phase8ManagedOperationRegistry.RequireDigest(TargetSetDigest);
        if (CheckpointId != BuildCheckpointId(ReceiptId) ||
            RestoreEntries.Count is < 1 or > Phase8ManagedOperationContract.MaxOperations ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw InvalidCheckpoint("Checkpoint v2 binding is invalid.");
        }
        foreach (var entry in RestoreEntries)
        {
            Phase8ManagedOperationRegistry.RequireIdentifier(entry.OperationId, 128);
            entry.TargetBefore.Validate();
            Phase8ManagedOperationRegistry.RequireDigest(entry.FingerprintAfter);
            Phase8ManagedOperationRegistry.RequireIdentifier(entry.Space, 64);
            Phase8ManagedOperationRegistry.RequireIdentifier(entry.SnapshotId, 128);
            Phase8ManagedOperationRegistry.RequireDigest(entry.DependencyClosureDigest);
            Phase8ManagedOperationRegistry.RequireDigest(entry.DescriptorDigest);
            entry.RestoreDescriptor.Validate();
            if (entry.TargetBefore.EntityType != entry.RestoreDescriptor.EntityType ||
                entry.Space != "MODEL_SPACE" ||
                entry.SnapshotId != SnapshotId ||
                entry.Dependencies.Count is < 1 or > 16 ||
                entry.DependencyClosureDigest !=
                    CadRestoreEvidenceDigest.Dependencies(entry.Dependencies) ||
                entry.DescriptorDigest !=
                    CadRestoreEvidenceDigest.Descriptor(entry.RestoreDescriptor))
            {
                throw InvalidCheckpoint(
                    "Checkpoint v2 descriptor provenance or dependency closure is invalid.");
            }
            foreach (var dependency in entry.Dependencies)
            {
                if (dependency.Kind is not ("layer" or "linetype") ||
                    string.IsNullOrWhiteSpace(dependency.Name) ||
                    dependency.Name.Length > 255)
                {
                    throw InvalidCheckpoint("Checkpoint v2 dependency is invalid.");
                }
                Phase8ManagedOperationRegistry.RequireDigest(dependency.Fingerprint);
            }
        }
        ValidatePins(RuntimePins);
        if (RestoreBudgetBytes is < 1 or > Phase8ManagedOperationContract.MaxRestoreBytes)
        {
            if (includeDigest)
            {
                throw InvalidCheckpoint("Checkpoint v2 restore budget is invalid.");
            }
        }
        if (includeDigest)
        {
            Phase8ManagedOperationRegistry.RequireDigest(CheckpointDigest);
            if (CheckpointDigest != ComputeDigest())
            {
                throw InvalidCheckpoint("Checkpoint v2 digest is invalid.");
            }
        }
    }

    private string SerializeCore(bool includeDigest) => JsonSerializer.Serialize(
        includeDigest
            ? this
            : this with { CheckpointDigest = $"sha256:{new string('0', 64)}" },
        HostProtocol.JsonOptions);

    private static void ValidatePins(CadManagedRuntimePins pins)
    {
        try
        {
            CadManagedRuntimePinValidation.Validate(pins);
        }
        catch (ProtocolValidationException)
        {
            throw InvalidCheckpoint("Checkpoint v2 runtime pins are invalid.");
        }
    }

    private static void RequireExact(JsonElement value, IReadOnlySet<string> fields)
    {
        if (value.ValueKind != JsonValueKind.Object ||
            value.EnumerateObject().Count() != fields.Count ||
            value.EnumerateObject().Any(property => !fields.Contains(property.Name)))
        {
            throw InvalidCheckpoint("Checkpoint v2 has unknown or missing fields.");
        }
    }

    private static void ValidateRestoreShape(JsonElement root)
    {
        RequireExact(
            root.GetProperty("runtime_pins"),
            new HashSet<string>(StringComparer.Ordinal)
            {
                "runtime_id", "host_family", "host_version", "package_hash",
                "operation_registry_version", "operation_registry_hash",
                "compiler_hash", "capability_manifest_hash",
                "capability_evidence_hash", "policy_digest",
                "rollout_policy_digest"
            });
        foreach (var entry in root.GetProperty("restore_entries").EnumerateArray())
        {
            RequireExact(
                entry,
                new HashSet<string>(StringComparer.Ordinal)
                {
                    "operation_id", "target_before", "fingerprint_after",
                    "space", "snapshot_id", "dependencies",
                    "dependency_closure_digest", "restore_descriptor",
                    "descriptor_digest"
                });
            RequireExact(
                entry.GetProperty("target_before"),
                new HashSet<string>(StringComparer.Ordinal)
                {
                    "document_id", "entity_id", "entity_type", "fingerprint"
                });
            var descriptor = entry.GetProperty("restore_descriptor");
            foreach (var dependency in entry.GetProperty("dependencies").EnumerateArray())
            {
                RequireExact(
                    dependency,
                    new HashSet<string>(StringComparer.Ordinal)
                    {
                        "kind", "name", "fingerprint"
                    });
            }
            RequireExact(
                descriptor,
                new HashSet<string>(StringComparer.Ordinal)
                {
                    "descriptor_schema", "restore_strategy", "entity_type",
                    "style", "line_start", "line_end",
                    "circle_center", "circle_normal", "circle_radius",
                    "polyline_elevation", "polyline_closed", "polyline_vertices"
                });
            RequireExact(
                descriptor.GetProperty("style"),
                new HashSet<string>(StringComparer.Ordinal)
                {
                    "layer", "linetype", "linetype_scale", "line_weight",
                    "visible", "color_index"
                });
            RequirePointOrNull(descriptor.GetProperty("line_start"));
            RequirePointOrNull(descriptor.GetProperty("line_end"));
            RequirePointOrNull(descriptor.GetProperty("circle_center"));
            RequirePointOrNull(descriptor.GetProperty("circle_normal"));
            var vertices = descriptor.GetProperty("polyline_vertices");
            if (vertices.ValueKind == JsonValueKind.Array)
            {
                foreach (var vertex in vertices.EnumerateArray())
                {
                    RequireExact(
                        vertex,
                        new HashSet<string>(StringComparer.Ordinal)
                        {
                            "x", "y", "bulge"
                        });
                }
            }
            else if (vertices.ValueKind != JsonValueKind.Null)
            {
                throw InvalidCheckpoint("Checkpoint v2 polyline vertices are malformed.");
            }
        }
    }

    private static void RequirePointOrNull(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Null)
        {
            return;
        }
        RequireExact(
            value,
            new HashSet<string>(StringComparer.Ordinal)
            {
                "x", "y", "z"
            });
    }

    private static ProtocolValidationException InvalidCheckpoint(string message) =>
        new("rollback_conflict", message);
}

public sealed record CadManagedOutputMapping(
    string OperationId,
    string OutputId,
    string EntityId,
    string EntityType,
    string Layer,
    string Fingerprint);

public sealed record CadManagedModifiedEntity(
    string OperationId,
    string EntityId,
    string EntityType,
    string FingerprintBefore,
    string FingerprintAfter);

public sealed record CadManagedOperationReceipt(
    string ReceiptId,
    string PlanDigest,
    string EffectDigest,
    string EffectIdentityDigest,
    string TargetSetDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<CadManagedOutputMapping> CreatedOutputs,
    IReadOnlyList<CadManagedModifiedEntity> ModifiedEntities,
    string? CheckpointV1Id,
    string? CheckpointV1Digest,
    string? CheckpointV2Id,
    string? CheckpointV2Digest,
    CadManagedRuntimePins Pins)
{
    public void Validate()
    {
        Phase8ManagedOperationRegistry.RequireIdentifier(ReceiptId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(PlanDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectIdentityDigest);
        Phase8ManagedOperationRegistry.RequireDigest(TargetSetDigest);
        CadManagedRuntimePinValidation.Validate(Pins);
        if (CreatedOutputs.Count > Phase8ManagedOperationContract.MaxOutputs ||
            ModifiedEntities.Count > Phase8ManagedOperationContract.MaxOperations)
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Managed receipt exceeds bounded operation results.");
        }
        if (CreatedOutputs.Count != 0 &&
            (string.IsNullOrEmpty(CheckpointV1Id) ||
             string.IsNullOrEmpty(CheckpointV1Digest)))
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Create-equivalent outputs require checkpoint v1 ownership material.");
        }
        if (CheckpointV1Digest is not null)
        {
            Phase8ManagedOperationRegistry.RequireDigest(CheckpointV1Digest);
        }
        if (ModifiedEntities.Count != 0 &&
            (string.IsNullOrEmpty(CheckpointV2Id) ||
             string.IsNullOrEmpty(CheckpointV2Digest)))
        {
            throw Phase8ManagedOperationRegistry.Invalid(
                "Modified entities require checkpoint v2.");
        }
        if (CheckpointV2Digest is not null)
        {
            Phase8ManagedOperationRegistry.RequireDigest(CheckpointV2Digest);
        }
    }
}

public sealed record CadManagedCommitRecord(
    string SchemaVersion,
    CadManagedOperationReceipt Receipt,
    string CreatedAt,
    string ReceiptDigest)
{
    public static CadManagedCommitRecord Create(
        CadManagedOperationReceipt receipt,
        DateTimeOffset createdAt)
    {
        receipt.Validate();
        var value = new CadManagedCommitRecord(
            Phase8ManagedOperationContract.ReceiptVersion,
            receipt,
            createdAt.ToUniversalTime().ToString("O"),
            $"sha256:{new string('0', 64)}");
        return value with { ReceiptDigest = value.ComputeDigest() };
    }

    public static CadManagedCommitRecord Parse(string json)
    {
        if (Encoding.UTF8.GetByteCount(json) >
            Phase8ManagedOperationContract.MaxRestoreBytes + 8192)
        {
            throw InvalidRecord();
        }
        try
        {
            using var document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 16
            });
            var fields = new HashSet<string>(StringComparer.Ordinal)
            {
                "schema_version", "receipt", "created_at", "receipt_digest"
            };
            if (document.RootElement.ValueKind != JsonValueKind.Object ||
                document.RootElement.EnumerateObject().Count() != fields.Count ||
                document.RootElement.EnumerateObject()
                    .Any(property => !fields.Contains(property.Name)))
            {
                throw InvalidRecord();
            }
            ValidateReceiptShape(document.RootElement.GetProperty("receipt"));
            var value = JsonSerializer.Deserialize<CadManagedCommitRecord>(
                document.RootElement.GetRawText(),
                HostProtocol.JsonOptions) ?? throw InvalidRecord();
            value.Validate();
            return value;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException or FormatException)
        {
            throw InvalidRecord();
        }
    }

    public string Serialize()
    {
        Validate();
        return JsonSerializer.Serialize(this, HostProtocol.JsonOptions);
    }

    public static string BuildCreatedOutputOwnershipDigest(
        string receiptId,
        IReadOnlyList<CadManagedOutputMapping> outputs)
    {
        Phase8ManagedOperationRegistry.RequireIdentifier(receiptId, 128);
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                checkpoint_schema = Phase7RollbackContract.CheckpointVersion,
                receipt_id = receiptId,
                created_outputs = outputs
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private void Validate()
    {
        if (SchemaVersion != Phase8ManagedOperationContract.ReceiptVersion ||
            !DateTimeOffset.TryParse(CreatedAt, out _))
        {
            throw InvalidRecord();
        }
        Receipt.Validate();
        Phase8ManagedOperationRegistry.RequireDigest(ReceiptDigest);
        if (ReceiptDigest != ComputeDigest())
        {
            throw InvalidRecord();
        }
    }

    private string ComputeDigest()
    {
        using var document = JsonSerializer.SerializeToDocument(
            this with { ReceiptDigest = $"sha256:{new string('0', 64)}" },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static void ValidateReceiptShape(JsonElement receipt)
    {
        RequireExact(
            receipt,
            [
                "receipt_id", "plan_digest", "effect_digest",
                "effect_identity_digest", "target_set_digest", "document_id",
                "document_revision_before", "document_revision_after",
                "created_outputs", "modified_entities", "checkpoint_v1_id",
                "checkpoint_v1_digest", "checkpoint_v2_id",
                "checkpoint_v2_digest", "pins"
            ]);
        foreach (var output in receipt.GetProperty("created_outputs").EnumerateArray())
        {
            RequireExact(
                output,
                [
                    "operation_id", "output_id", "entity_id", "entity_type",
                    "layer", "fingerprint"
                ]);
        }
        foreach (var entity in receipt.GetProperty("modified_entities").EnumerateArray())
        {
            RequireExact(
                entity,
                [
                    "operation_id", "entity_id", "entity_type",
                    "fingerprint_before", "fingerprint_after"
                ]);
        }
        RequireExact(
            receipt.GetProperty("pins"),
            [
                "runtime_id", "host_family", "host_version", "package_hash",
                "operation_registry_version", "operation_registry_hash",
                "compiler_hash", "capability_manifest_hash",
                "capability_evidence_hash", "policy_digest",
                "rollout_policy_digest"
            ]);
    }

    private static void RequireExact(
        JsonElement value,
        IEnumerable<string> fields)
    {
        var expected = fields.ToHashSet(StringComparer.Ordinal);
        if (value.ValueKind != JsonValueKind.Object ||
            value.EnumerateObject().Count() != expected.Count ||
            value.EnumerateObject().Any(property => !expected.Contains(property.Name)))
        {
            throw InvalidRecord();
        }
    }

    private static ProtocolValidationException InvalidRecord() =>
        new("ledger_corrupt", "Drawing contains an invalid Phase 8 operation receipt.");
}

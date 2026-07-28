using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutocadMcp.Host.Core;

public static class Phase8ManagedAdmissionContract
{
    public const string SealedPlanVersion = "cad.execution-plan/1";
    public const string EffectIdentityDomain = "cad.effect-identity/1";
    public const string TargetSetDomain = "cad.target-set/1";
    public const string OperationPayloadDomain = "cad.host-operation-payload/1";
}

public static class CadManagedRuntimePinValidation
{
    public static void Validate(CadManagedRuntimePins pins)
    {
        if (pins.RuntimeId != "managed_dotnet" ||
            pins.HostFamily != "R25" ||
            string.IsNullOrWhiteSpace(pins.HostVersion) ||
            pins.OperationRegistryVersion !=
                Phase8ManagedOperationContract.RegistryVersion)
        {
            throw new ProtocolValidationException(
                "runtime_changed",
                "Managed R25 runtime pins are invalid.");
        }
        Phase8ManagedOperationRegistry.RequireDigest(pins.PackageHash);
        Phase8ManagedOperationRegistry.RequireDigest(pins.OperationRegistryHash);
        Phase8ManagedOperationRegistry.RequireDigest(pins.CompilerHash);
        Phase8ManagedOperationRegistry.RequireDigest(pins.CapabilityManifestHash);
        Phase8ManagedOperationRegistry.RequireDigest(pins.CapabilityEvidenceHash);
        Phase8ManagedOperationRegistry.RequireDigest(pins.PolicyDigest);
        Phase8ManagedOperationRegistry.RequireDigest(pins.RolloutPolicyDigest);
    }
}

public sealed record CadManagedSealedPlan(
    string SchemaVersion,
    string PlanDigest,
    string EffectDigest,
    string TargetSetDigest,
    string OperationPayloadDigest,
    string CheckpointStrategyDigest,
    string ValidationProfileDigest,
    string OwnerId,
    string DeviceId,
    string DocumentId,
    string SnapshotId,
    string DocumentRevision,
    CadManagedRuntimePins Pins,
    IReadOnlyList<CadManagedOperation> Operations)
{
    public void Validate()
    {
        if (SchemaVersion != Phase8ManagedAdmissionContract.SealedPlanVersion)
        {
            throw Invalid("Host accepts only a sealed cad.execution-plan/1.");
        }
        Phase8ManagedOperationRegistry.RequireDigest(PlanDigest);
        Phase8ManagedOperationRegistry.RequireDigest(EffectDigest);
        Phase8ManagedOperationRegistry.RequireDigest(TargetSetDigest);
        Phase8ManagedOperationRegistry.RequireDigest(OperationPayloadDigest);
        Phase8ManagedOperationRegistry.RequireDigest(CheckpointStrategyDigest);
        Phase8ManagedOperationRegistry.RequireDigest(ValidationProfileDigest);
        Phase8ManagedOperationRegistry.RequireIdentifier(OwnerId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DeviceId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(SnapshotId, 128);
        Phase8ManagedOperationRegistry.RequireIdentifier(DocumentRevision, 256);
        CadManagedRuntimePinValidation.Validate(Pins);
        Phase8ManagedOperationRegistry.ValidateBatch(Operations);
        if (Operations.Any(operation =>
                operation.Target.DocumentId != DocumentId))
        {
            throw new ProtocolValidationException(
                "document_changed",
                "Sealed plan target belongs to another drawing.");
        }
        if (TargetSetDigest != ComputeTargetSetDigest(Operations) ||
            OperationPayloadDigest != ComputeOperationPayloadDigest(Operations))
        {
            throw Invalid("Sealed plan target or operation payload digest changed.");
        }
    }

    public static string ComputeTargetSetDigest(
        IReadOnlyList<CadManagedOperation> operations)
    {
        var targets = operations.Select((operation, index) => new
        {
            ordinal = index,
            operation_id = operation.OperationId,
            role = Phase8ManagedOperationRegistry
                .Require(operation.Kind, operation.Target.EntityType)
                .EffectClass == CadOperationEffectClass.CreateEquivalent
                    ? "source"
                    : "target",
            document_id = operation.Target.DocumentId,
            entity_id = operation.Target.EntityId,
            entity_type = operation.Target.EntityType,
            fingerprint = operation.Target.Fingerprint
        }).ToArray();
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                domain = Phase8ManagedAdmissionContract.TargetSetDomain,
                targets
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    public static string ComputeOperationPayloadDigest(
        IReadOnlyList<CadManagedOperation> operations)
    {
        var values = operations.Select(OperationIdentity).ToArray();
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                domain = Phase8ManagedAdmissionContract.OperationPayloadDomain,
                operations = values
            },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static JsonObject OperationIdentity(CadManagedOperation operation)
    {
        var value = new JsonObject
        {
            ["kind"] = operation.Kind,
            ["operation_id"] = operation.OperationId,
            ["target"] = JsonSerializer.SerializeToNode(
                operation.Target,
                HostProtocol.JsonOptions)
        };
        switch (operation)
        {
            case CadCopyEntityOperation copy:
                value["displacement"] = JsonSerializer.SerializeToNode(
                    copy.Displacement,
                    HostProtocol.JsonOptions);
                value["output_id"] = copy.OutputId;
                break;
            case CadLinearPatternOperation linear:
                value["step"] = JsonSerializer.SerializeToNode(
                    linear.Step,
                    HostProtocol.JsonOptions);
                value["count"] = linear.Count;
                value["output_ids"] = JsonSerializer.SerializeToNode(
                    linear.StableOutputIds,
                    HostProtocol.JsonOptions);
                break;
            case CadRectangularPatternOperation rectangular:
                value["column_step"] = JsonSerializer.SerializeToNode(
                    rectangular.ColumnStep,
                    HostProtocol.JsonOptions);
                value["column_count"] = rectangular.ColumnCount;
                value["row_step"] = JsonSerializer.SerializeToNode(
                    rectangular.RowStep,
                    HostProtocol.JsonOptions);
                value["row_count"] = rectangular.RowCount;
                value["output_ids"] = JsonSerializer.SerializeToNode(
                    rectangular.StableOutputIds,
                    HostProtocol.JsonOptions);
                break;
            case CadPolarPatternOperation polar:
                value["center"] = JsonSerializer.SerializeToNode(
                    polar.Center,
                    HostProtocol.JsonOptions);
                value["count"] = polar.Count;
                value["total_angle_radians"] = polar.TotalAngleRadians;
                value["rotate_items"] = polar.RotateItems;
                value["output_ids"] = JsonSerializer.SerializeToNode(
                    polar.StableOutputIds,
                    HostProtocol.JsonOptions);
                break;
            case CadOffsetEntityOperation offset:
                value["signed_distance"] = offset.SignedDistance;
                value["output_id"] = offset.OutputId;
                break;
            case CadMoveEntityOperation move:
                value["displacement"] = JsonSerializer.SerializeToNode(
                    move.Displacement,
                    HostProtocol.JsonOptions);
                break;
            case CadRotateEntityOperation rotate:
                value["base_point"] = JsonSerializer.SerializeToNode(
                    rotate.BasePoint,
                    HostProtocol.JsonOptions);
                value["angle_radians"] = rotate.AngleRadians;
                break;
            case CadScaleEntityOperation scale:
                value["base_point"] = JsonSerializer.SerializeToNode(
                    scale.BasePoint,
                    HostProtocol.JsonOptions);
                value["uniform_factor"] = scale.UniformFactor;
                break;
            default:
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Operation is outside the explicit Managed R25 registry.");
        }
        return value;
    }

    private static ProtocolValidationException Invalid(string message) =>
        new("plan_mismatch", message);
}

public sealed record CadManagedHostAdmission(
    CadManagedRuntimePins ActualPins,
    IReadOnlySet<string> TrustedCapabilityKeys,
    IReadOnlySet<string> AllowedOperationKinds,
    bool IndependentCapabilityEvidenceVerified,
    bool CreatePackEnabled,
    bool CheckpointV2Enabled,
    bool TransformPackEnabled)
{
    public static CadManagedHostAdmission DefaultDeny(CadManagedRuntimePins actualPins) =>
        new(
            actualPins,
            new HashSet<string>(StringComparer.Ordinal),
            new HashSet<string>(StringComparer.Ordinal),
            IndependentCapabilityEvidenceVerified: false,
            CreatePackEnabled: false,
            CheckpointV2Enabled: false,
            TransformPackEnabled: false);

    public void AssertAllowed(CadManagedSealedPlan plan)
    {
        plan.Validate();
        AssertPins(plan.Pins, ActualPins);
        if (!IndependentCapabilityEvidenceVerified ||
            ActualPins.OperationRegistryHash !=
                Phase8ManagedOperationRegistry.RegistryDigest)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Host capability evidence or compiled operation registry is unverified.");
        }
        var effectClasses = plan.Operations
            .Select(operation => Phase8ManagedOperationRegistry.Require(
                operation.Kind,
                operation.Target.EntityType).EffectClass)
            .ToHashSet();
        if (effectClasses.Count > 1)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Mixed create-equivalent and exact-transform plans remain disabled until a compound atomic rollback contract is available.");
        }
        foreach (var operation in plan.Operations)
        {
            var descriptor = Phase8ManagedOperationRegistry.Require(
                operation.Kind,
                operation.Target.EntityType);
            var entityCapability = descriptor.CapabilityKey.Replace(
                "line-circle-lwpolyline",
                operation.Target.EntityType.ToLowerInvariant(),
                StringComparison.Ordinal);
            if (!AllowedOperationKinds.Contains(operation.Kind) ||
                (!TrustedCapabilityKeys.Contains(descriptor.CapabilityKey) &&
                 !TrustedCapabilityKeys.Contains(entityCapability)))
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Operation is absent from independently admitted Host capability.");
            }
            if (descriptor.EffectClass == CadOperationEffectClass.CreateEquivalent &&
                !CreatePackEnabled)
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Managed create-equivalent pack is disabled.");
            }
            if (descriptor.EffectClass == CadOperationEffectClass.ExactTransform &&
                (!TransformPackEnabled || !CheckpointV2Enabled))
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Managed transform pack remains disabled without checkpoint v2.");
            }
        }
    }

    private static void AssertPins(
        CadManagedRuntimePins expected,
        CadManagedRuntimePins actual)
    {
        if (expected != actual)
        {
            throw new ProtocolValidationException(
                "runtime_changed",
                "Runtime, capability, compiler, registry, policy, or rollout pin changed.");
        }
    }
}

public sealed record CadManagedEffectIdentity(
    string ReceiptId,
    string EffectIdentityDigest)
{
    public void Validate()
    {
        Phase8ManagedOperationRegistry.RequireIdentifier(ReceiptId, 128);
        Phase8ManagedOperationRegistry.RequireDigest(EffectIdentityDigest);
    }

    public static CadManagedEffectIdentity Create(CadManagedSealedPlan plan)
    {
        plan.Validate();
        var receiptSeed = DomainHash(
            "cad.operation-receipt-id/1",
            new
            {
                plan.PlanDigest,
                plan.EffectDigest,
                plan.TargetSetDigest,
                plan.OperationPayloadDigest,
                plan.DocumentId,
                plan.DocumentRevision,
                plan.Pins
            });
        var receiptId = $"AUTOCAD_MCP_PHASE8_{receiptSeed[7..39]}";
        var identity = DomainHash(
            Phase8ManagedAdmissionContract.EffectIdentityDomain,
            new
            {
                action = "commit",
                receipt_id = receiptId,
                plan.PlanDigest,
                plan.EffectDigest,
                plan.TargetSetDigest,
                plan.OperationPayloadDigest,
                plan.CheckpointStrategyDigest,
                plan.ValidationProfileDigest,
                plan.DocumentId,
                document_revision_before = plan.DocumentRevision,
                operation_packs = plan.Operations
                    .Select(operation => new
                    {
                        operation.Kind,
                        version = 1,
                        strategy = Phase8ManagedOperationRegistry
                            .Require(operation.Kind, operation.Target.EntityType)
                            .CheckpointStrategy.ToString()
                    }).ToArray(),
                plan.Pins
            });
        var value = new CadManagedEffectIdentity(receiptId, identity);
        value.Validate();
        return value;
    }

    private static string DomainHash(string domain, object value)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new { domain, value },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }
}

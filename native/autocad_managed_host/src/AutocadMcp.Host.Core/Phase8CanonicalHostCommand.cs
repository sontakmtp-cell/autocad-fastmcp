using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutocadMcp.Host.Core;

public sealed record Phase8HostRuntimeEvidence(
    string RuntimeId,
    string RuntimeRole,
    string HostFamily,
    string HostVersion,
    string PackageId,
    string PackageVersion,
    string PackageHash,
    string OperationRegistryVersion,
    string OperationRegistryHash,
    string HostEvidenceDigest)
{
    public static Phase8HostRuntimeEvidence Create(
        string hostVersion,
        string packageId,
        string packageVersion,
        string packageHash)
    {
        var value = new Phase8HostRuntimeEvidence(
            "managed_dotnet",
            "primary",
            "R25",
            hostVersion,
            packageId,
            packageVersion,
            packageHash,
            Phase8CanonicalHostRegistry.Version,
            Phase8CanonicalHostRegistry.Digest,
            $"sha256:{new string('0', 64)}");
        return value with
        {
            HostEvidenceDigest = Phase8CanonicalHostRegistry.HostEvidenceDigest(value)
        };
    }
}

public static class Phase8CanonicalHostRegistry
{
    public const string Version = Phase8ManagedOperationContract.RegistryVersion;

    public static string Digest { get; } =
        Phase8ManagedOperationRegistry.RegistryDigest;

    public static string HostEvidenceDigest(Phase8HostRuntimeEvidence runtime) =>
        DomainHash(
            "cad.host-capability-evidence/1",
            new
            {
                runtime.RuntimeId,
                runtime.RuntimeRole,
                runtime.HostFamily,
                runtime.HostVersion,
                runtime.PackageId,
                runtime.PackageVersion,
                runtime.PackageHash,
                runtime.OperationRegistryVersion,
                runtime.OperationRegistryHash
            });

    private static string DomainHash(string domain, object payload)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new { domain, payload },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }
}

public sealed record Phase8CapabilityClaim(
    string EvidenceId,
    string CapabilityKey,
    string OperationPack,
    string EntityType,
    string SupportState,
    string EvidenceDigest);

public sealed record Phase8ApprovalClaim(
    string IntentDigest,
    string ApprovalProofDigest,
    string CommandId,
    string IdempotencyKey,
    string ExecutionBindingDigest,
    string PreviewId,
    string PreviewDigest,
    DateTimeOffset PreviewExpiresAt,
    string ReceiptId);

public sealed record Phase8CanonicalHostCommand(
    string OperationId,
    JsonElement ExecutionPlan,
    Phase8PlanDigests Digests,
    string PlanId,
    string DeviceId,
    string DocumentId,
    string ExpectedDocumentRevision,
    string ValidationProfilesDigest,
    string CheckpointStrategyDigest,
    string HardBudgetsDigest,
    string RolloutPolicyDigest,
    IReadOnlyList<Phase8CapabilityClaim> CapabilityEvidence,
    Phase8ApprovalClaim? Approval)
{
    public bool HasTargetOperations => ExecutionPlan.GetProperty("operations")
        .EnumerateArray()
        .Any(item => Phase8Contract.TargetKinds.Contains(
            item.GetProperty("kind").GetString()!));

    public CadProgramV02 ToCreateProgram()
    {
        var operations = ExecutionPlan.GetProperty("operations")
            .EnumerateArray()
            .Select(ParseCreateOperation)
            .ToArray();
        var budgets = ExecutionPlan.GetProperty("budgets");
        var defaults = CadProgramBudgets.Defaults;
        return new(
            ExecutionPlan.GetProperty("source_program_id").GetString()!,
            ExecutionPlan.GetProperty("source_program_revision").GetInt64(),
            DeviceId,
            ExecutionPlan.GetProperty("source_snapshot_id").GetString()!,
            DocumentId,
            ExpectedDocumentRevision,
            operations,
            [],
            defaults with
            {
                MaxOperations = budgets.GetProperty("hard_max_operations").GetInt32(),
                MaxEntities = budgets.GetProperty("hard_max_entities").GetInt32(),
                MaxVertices = budgets.GetProperty("hard_max_vertices").GetInt32(),
                MaxTextBytes = budgets.GetProperty("hard_max_text_bytes").GetInt32()
            },
            Digests.SourceDigest);
    }

    public string EffectIdentityDigest()
    {
        if (Approval is null)
        {
            throw new ProtocolValidationException(
                "approval_required",
                "Phase 8 commit requires trusted Phase 7 approval.");
        }
        var pins = ExecutionPlan.GetProperty("execution_pins");
        var operationPacks = CapabilityEvidence
            .Select(item => item.OperationPack)
            .Distinct(StringComparer.Ordinal)
            .Order(StringComparer.Ordinal)
            .ToArray();
        var evidenceDigests = CapabilityEvidence
            .Select(item => item.EvidenceDigest)
            .Order(StringComparer.Ordinal)
            .ToArray();
        var capabilityStates = CapabilityEvidence
            .OrderBy(item => item.CapabilityKey, StringComparer.Ordinal)
            .ToDictionary(
                item => item.CapabilityKey,
                item => item.SupportState,
                StringComparer.Ordinal);
        var capabilityStateHash = DomainHash(
            "cad.capability-intersection/1",
            new { capability_states = capabilityStates });
        var payload = new JsonObject
        {
            ["action"] = "program_commit",
            ["intent_digest"] = Approval.IntentDigest,
            ["approval_proof_digest"] = Approval.ApprovalProofDigest,
            ["idempotency_key"] = Approval.IdempotencyKey,
            ["execution_binding_digest"] = Approval.ExecutionBindingDigest,
            ["execution_plan_digest"] = Digests.ExecutionPlanDigest,
            ["effect_manifest_digest"] = Digests.EffectManifestDigest,
            ["target_refs_digest"] = Digests.TargetRefsDigest,
            ["document_id"] = DocumentId,
            ["document_revision_before"] = ExpectedDocumentRevision,
            ["preview_id"] = Approval.PreviewId,
            ["preview_digest"] = Approval.PreviewDigest,
            ["preview_expires_at"] =
                Approval.PreviewExpiresAt.ToUniversalTime().ToString("O"),
            ["receipt_id"] = Approval.ReceiptId,
            ["checkpoint_strategy_digest"] = CheckpointStrategyDigest,
            ["operation_packs"] = JsonSerializer.SerializeToNode(
                operationPacks,
                HostProtocol.JsonOptions),
            ["capability_evidence_digests"] = JsonSerializer.SerializeToNode(
                evidenceDigests,
                HostProtocol.JsonOptions),
            ["capability_state_hash"] = capabilityStateHash
        };
        foreach (var pin in pins.EnumerateObject())
        {
            payload[pin.Name] = JsonNode.Parse(pin.Value.GetRawText());
        }
        return DomainHash("cad.effect-identity/1", payload);
    }

    public string PreviewEvidenceDigest(JsonElement previewEvidence) =>
        DomainHash(
            "cad.program.preview-evidence/1",
            new
            {
                execution_plan_digest = Digests.ExecutionPlanDigest,
                effect_manifest_digest = Digests.EffectManifestDigest,
                target_refs_digest = Digests.TargetRefsDigest,
                validation_profiles_digest = ValidationProfilesDigest,
                checkpoint_strategy_digest = CheckpointStrategyDigest,
                hard_budgets_digest = HardBudgetsDigest,
                rollout_policy_digest = RolloutPolicyDigest,
                preview_evidence = JsonNode.Parse(previewEvidence.GetRawText())
            });

    public CadManagedSealedPlan ToManagedPlan()
    {
        var refs = ExecutionPlan.GetProperty("materialized_target_refs")
            .EnumerateArray()
            .ToDictionary(
                item => item.GetProperty("ref_id").GetString()!,
                item => item,
                StringComparer.Ordinal);
        if (refs.Count == 0)
        {
            throw new ProtocolValidationException(
                "program_invalid",
                "Managed target plan requires exact materialized target refs.");
        }
        var operations = ExecutionPlan.GetProperty("operations")
            .EnumerateArray()
            .Select(item => ParseManagedOperation(item, refs))
            .ToArray();
        var pins = ExecutionPlan.GetProperty("execution_pins");
        var runtimePins = new CadManagedRuntimePins(
            pins.GetProperty("runtime_id").GetString()!,
            pins.GetProperty("host_family").GetString()!,
            pins.GetProperty("host_version").GetString()!,
            pins.GetProperty("package_hash").GetString()!,
            pins.GetProperty("operation_registry_version").GetString()!,
            pins.GetProperty("operation_registry_hash").GetString()!,
            Digests.CompilerDigest,
            pins.GetProperty("capability_manifest_hash").GetString()!,
            DomainHash(
                "cad.capability-evidence-set/1",
                CapabilityEvidence.Select(item => item.EvidenceDigest)
                    .Order(StringComparer.Ordinal).ToArray()),
            DomainHash(
                "cad.policy-version/1",
                pins.GetProperty("policy_version").GetString()!),
            pins.GetProperty("rollout_policy_digest").GetString()!);
        return new CadManagedSealedPlan(
            Phase8ManagedAdmissionContract.SealedPlanVersion,
            Digests.ExecutionPlanDigest,
            Digests.EffectManifestDigest,
            CadManagedSealedPlan.ComputeTargetSetDigest(operations),
            CadManagedSealedPlan.ComputeOperationPayloadDigest(operations),
            CheckpointStrategyDigest,
            ValidationProfilesDigest,
            refs.Values.Select(item => item.GetProperty("owner_id").GetString()!)
                .Distinct(StringComparer.Ordinal).Single(),
            DeviceId,
            DocumentId,
            ExecutionPlan.GetProperty("source_snapshot_id").GetString()!,
            ExpectedDocumentRevision,
            runtimePins,
            operations);
    }

    private static string DomainHash(string domain, object payload)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new { domain, payload },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static CadCreateOperation ParseCreateOperation(JsonElement operation)
    {
        var id = operation.GetProperty("operation_id").GetString()!;
        return operation.GetProperty("kind").GetString() switch
        {
            "ensure_layer" => new EnsureLayerOperation(
                id,
                operation.GetProperty("name").GetString()!,
                operation.TryGetProperty("color_index", out var color)
                    ? color.GetInt16()
                    : null),
            "create_line" => new CreateLineOperation(
                id,
                operation.GetProperty("layer").GetString()!,
                Point(operation.GetProperty("start")),
                Point(operation.GetProperty("end"))),
            "create_circle" => new CreateCircleOperation(
                id,
                operation.GetProperty("layer").GetString()!,
                Point(operation.GetProperty("center")),
                Number(operation, "radius_mm")),
            "create_polyline" => new CreatePolylineOperation(
                id,
                operation.GetProperty("layer").GetString()!,
                operation.GetProperty("vertices").EnumerateArray()
                    .Select(Point).ToArray(),
                operation.GetProperty("closed").GetBoolean()),
            "create_rectangle" => new CreateRectangleOperation(
                id,
                operation.GetProperty("layer").GetString()!,
                Point(operation.GetProperty("first_corner")),
                Point(operation.GetProperty("opposite_corner"))),
            "create_text" => new CreateTextOperation(
                id,
                operation.GetProperty("layer").GetString()!,
                Point(operation.GetProperty("position")),
                operation.GetProperty("text").GetString()!,
                Number(operation, "height_mm"),
                Number(operation, "rotation_rad")),
            "create_dimension_linear" => new CreateDimensionLinearOperation(
                id,
                operation.GetProperty("layer").GetString()!,
                Point(operation.GetProperty("extension_line1_point")),
                Point(operation.GetProperty("extension_line2_point")),
                Point(operation.GetProperty("dimension_line_point")),
                operation.TryGetProperty("text_override", out var text)
                    ? text.GetString()
                    : null),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "Operation is outside the canonical create-only registry.")
        };
    }

    private static CadManagedOperation ParseManagedOperation(
        JsonElement operation,
        IReadOnlyDictionary<string, JsonElement> refs)
    {
        var kind = operation.GetProperty("kind").GetString()!;
        if (!Phase8Contract.TargetKinds.Contains(kind))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Target plan cannot mix create primitives with managed operations.");
        }
        var target = refs[operation.GetProperty("target_ref_id").GetString()!];
        var stableRef = new CadStableEntityRef(
            target.GetProperty("document_id").GetString()!,
            target.GetProperty("entity_id").GetString()!,
            target.GetProperty("entity_type").GetString()!,
            target.GetProperty("fingerprint").GetString()!);
        var operationId = operation.GetProperty("operation_id").GetString()!;
        return kind switch
        {
            "copy_entity" => new CadCopyEntityOperation(
                operationId,
                stableRef,
                Vector(operation.GetProperty("displacement_mm")),
                operation.GetProperty("output_id").GetString()!),
            "offset_entity" => new CadOffsetEntityOperation(
                operationId,
                stableRef,
                Number(operation, "signed_distance_mm"),
                operation.GetProperty("output_id").GetString()!),
            "move_entity" => new CadMoveEntityOperation(
                operationId,
                stableRef,
                Vector(operation.GetProperty("displacement_mm"))),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "Managed operation is outside the exact Phase 8 registry.")
        };
    }

    private static CadManagedVector Vector(JsonElement value) =>
        new(
            Number(value, "x_mm"),
            Number(value, "y_mm"),
            Number(value, "z_mm"));

    private static CadPoint Point(JsonElement value) =>
        new(
            Number(value, "x_mm"),
            Number(value, "y_mm"),
            Number(value, "z_mm"));

    private static double Number(JsonElement parent, string property) =>
        double.Parse(
            parent.GetProperty(property).GetString()!,
            NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture);
}

/// <summary>
/// The single Host boundary for cad.execution-plan/1. Validation delegates to
/// Phase8ContractValidator; this parser adds command, approval, and
/// independently observed Host-capability checks without introducing another
/// execution-plan shape.
/// </summary>
public static class Phase8CanonicalHostCommandParser
{
    private static readonly IReadOnlySet<string> ApprovalFields = Set(
        "schema_version", "action", "intent_id", "consent_id",
        "intent_digest", "approval_proof_digest", "device_id", "document_id",
        "document_revision", "job_id", "command_id", "idempotency_key",
        "source_digest", "execution_plan_digest", "execution_binding_digest",
        "expansion_digest", "effect_manifest_digest", "target_refs_digest",
        "validation_profiles_digest", "checkpoint_strategy_digest",
        "hard_budgets_digest", "preview_id", "preview_digest",
        "preview_expires_at", "receipt_id");

    private static readonly IReadOnlySet<string> EvidenceFields = Set(
        "schema_version", "evidence_id", "evidence_authority", "device_id",
        "capability_key", "operation_pack", "runtime_id", "host_family",
        "entity_type", "support_state", "package_hash",
        "capability_manifest_hash", "operation_registry_hash",
        "package_signature_verified", "agent_evidence_digest",
        "host_evidence_digest", "cohort", "evidence_version", "issued_at",
        "valid_until", "evidence_digest");

    public static bool IsPhase8(JsonElement arguments) =>
        arguments.ValueKind == JsonValueKind.Object &&
        arguments.TryGetProperty("execution_plan", out var plan) &&
        plan.ValueKind == JsonValueKind.Object &&
        plan.TryGetProperty("schema_version", out var schema) &&
        schema.ValueKind == JsonValueKind.String &&
        schema.GetString() == Phase8Contract.PlanSchemaVersion;

    public static Phase8CanonicalHostCommand Parse(
        string operationId,
        JsonElement arguments,
        Phase8HostRuntimeEvidence actualRuntime,
        DateTimeOffset now)
    {
        try
        {
            if (operationId is not (
                "cad.program.preview" or "cad.program.commit"))
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Canonical Phase 8 Host currently admits preview and commit only.");
            }
            EnsureExact(
                arguments,
                operationId == "cad.program.commit"
                    ? Set("execution_plan", "approval_binding", "capability_evidence")
                    : Set("execution_plan", "capability_evidence"));
            var plan = Object(arguments, "execution_plan");
            var digests = Phase8ContractValidator.ValidateExecutionPlan(plan);
            AssertCanonicalHostCapability(plan);
            AssertRuntime(plan.GetProperty("execution_pins"), actualRuntime);
            var claims = ParseCapabilityEvidence(
                Array(arguments, "capability_evidence", 1, 64),
                plan,
                actualRuntime,
                now,
                operationId == "cad.program.commit");
            var approval = operationId == "cad.program.commit"
                ? ParseApproval(Object(arguments, "approval_binding"), plan, digests, now)
                : null;
            return new(
                operationId,
                plan.Clone(),
                digests,
                Identifier(plan, "plan_id"),
                Identifier(plan, "device_id"),
                Identifier(plan, "document_id"),
                BoundedString(plan, "expected_document_revision", 256),
                Digest(plan, "validation_profiles_digest"),
                Digest(plan, "checkpoint_strategy_digest"),
                Digest(plan, "hard_budgets_digest"),
                Digest(plan.GetProperty("execution_pins"), "rollout_policy_digest"),
                claims,
                approval);
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception error) when (
            error is JsonException or InvalidOperationException or
            FormatException or OverflowException)
        {
            throw new ProtocolValidationException(
                "program_invalid",
                "Canonical Phase 8 Host command is malformed.");
        }
    }

    private static void AssertCanonicalHostCapability(JsonElement plan)
    {
        var budgets = plan.GetProperty("budgets");
        var operations = plan.GetProperty("operations").EnumerateArray().ToArray();
        var hasTargets = operations.Any(item =>
            Phase8Contract.TargetKinds.Contains(item.GetProperty("kind").GetString()!));
        var hasCreates = operations.Any(item =>
            Phase8Contract.CreateOnlyKinds.Contains(item.GetProperty("kind").GetString()!));
        if (hasTargets && hasCreates ||
            plan.GetProperty("effect_manifest").GetProperty("erases").GetInt32() != 0 ||
            operations.Length >
                CadProgramV02Contract.MaxOperations ||
            budgets.GetProperty("hard_max_operations").GetInt32() >
                CadProgramV02Contract.MaxOperations ||
            budgets.GetProperty("hard_max_entities").GetInt32() >
                CadProgramV02Contract.MaxEntities ||
            budgets.GetProperty("hard_max_vertices").GetInt32() >
                CadProgramV02Contract.MaxVertices ||
            budgets.GetProperty("hard_max_text_bytes").GetInt32() >
                CadProgramV02Contract.MaxTextBytes)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Plan is outside canonical Phase 8 Host capability/budgets.");
        }
    }

    private static void AssertRuntime(
        JsonElement pins,
        Phase8HostRuntimeEvidence actual)
    {
        var expected = new Dictionary<string, string>(StringComparer.Ordinal)
        {
            ["runtime_id"] = actual.RuntimeId,
            ["runtime_role"] = actual.RuntimeRole,
            ["host_family"] = actual.HostFamily,
            ["host_version"] = actual.HostVersion,
            ["package_id"] = actual.PackageId,
            ["package_version"] = actual.PackageVersion,
            ["package_hash"] = actual.PackageHash,
            ["operation_registry_version"] = actual.OperationRegistryVersion,
            ["operation_registry_hash"] = actual.OperationRegistryHash
        };
        foreach (var item in expected)
        {
            if (pins.GetProperty(item.Key).GetString() != item.Value)
            {
                throw new ProtocolValidationException(
                    "runtime_changed",
                    "Execution pin differs from independently observed Host runtime.");
            }
        }
        Digest(pins, "capability_manifest_hash");
        Identifier(pins, "policy_version");
        Digest(pins, "rollout_policy_digest");
    }

    private static IReadOnlyList<Phase8CapabilityClaim> ParseCapabilityEvidence(
        JsonElement evidence,
        JsonElement plan,
        Phase8HostRuntimeEvidence actual,
        DateTimeOffset now,
        bool commit)
    {
        var required = plan.GetProperty("required_capabilities")
            .EnumerateArray()
            .Select(item => item.GetString()!)
            .ToHashSet(StringComparer.Ordinal);
        var claims = new List<Phase8CapabilityClaim>();
        var evidenceIds = new HashSet<string>(StringComparer.Ordinal);
        var capabilityKeys = new HashSet<string>(StringComparer.Ordinal);
        var pins = plan.GetProperty("execution_pins");
        foreach (var item in evidence.EnumerateArray())
        {
            EnsureExact(item, EvidenceFields);
            String(item, "schema_version", "cad.capability-evidence/1");
            String(item, "evidence_authority", "gateway_server");
            var evidenceId = Identifier(item, "evidence_id");
            var capability = Identifier(item, "capability_key");
            if (!evidenceIds.Add(evidenceId) ||
                !capabilityKeys.Add(capability) ||
                !required.Contains(capability))
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Capability evidence is duplicate or outside the sealed plan.");
            }
            EvidenceString(
                item, "device_id", plan.GetProperty("device_id").GetString()!);
            EvidenceString(item, "runtime_id", actual.RuntimeId);
            EvidenceString(item, "host_family", actual.HostFamily);
            EvidenceString(item, "package_hash", actual.PackageHash);
            EvidenceString(
                item,
                "capability_manifest_hash",
                pins.GetProperty("capability_manifest_hash").GetString()!);
            EvidenceString(
                item, "operation_registry_hash", actual.OperationRegistryHash);
            EvidenceString(
                item, "host_evidence_digest", actual.HostEvidenceDigest);
            if (item.GetProperty("package_signature_verified").ValueKind !=
                JsonValueKind.True)
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Capability evidence lacks package-signature verification.");
            }
            Digest(item, "agent_evidence_digest");
            Identifier(item, "cohort");
            BoundedString(item, "evidence_version", 256);
            var issuedAt = Timestamp(item, "issued_at");
            var validUntil = Timestamp(item, "valid_until");
            if (issuedAt > now || validUntil <= now || validUntil <= issuedAt)
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Capability evidence is not currently valid.");
            }
            var support = BoundedString(item, "support_state", 32);
            if (SupportRank(support) < (commit ? 3 : 2))
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Capability evidence support state is below command minimum.");
            }
            var digest = Digest(item, "evidence_digest");
            var canonical = JsonNode.Parse(item.GetRawText())!.AsObject();
            canonical.Remove("evidence_digest");
            if (digest != DomainHash("cad.capability-evidence/1", canonical))
            {
                throw new ProtocolValidationException(
                    "capability_missing",
                    "Capability evidence digest is invalid.");
            }
            claims.Add(new(
                evidenceId,
                capability,
                BoundedString(item, "operation_pack", 256),
                BoundedString(item, "entity_type", 64),
                support,
                digest));
        }
        if (!required.SetEquals(capabilityKeys))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Sealed plan capabilities lack exact typed server evidence.");
        }
        return claims;
    }

    private static Phase8ApprovalClaim ParseApproval(
        JsonElement approval,
        JsonElement plan,
        Phase8PlanDigests digests,
        DateTimeOffset now)
    {
        EnsureExact(approval, ApprovalFields);
        String(approval, "schema_version", "cad.phase8-approval-binding/1");
        String(approval, "action", "program_commit");
        Identifier(approval, "intent_id");
        Identifier(approval, "consent_id");
        var intentDigest = Digest(approval, "intent_digest");
        var proofDigest = Digest(approval, "approval_proof_digest");
        String(approval, "device_id", plan.GetProperty("device_id").GetString()!);
        String(approval, "document_id", plan.GetProperty("document_id").GetString()!);
        String(
            approval,
            "document_revision",
            plan.GetProperty("expected_document_revision").GetString()!);
        Identifier(approval, "job_id");
        var commandId = Identifier(approval, "command_id");
        var idempotencyKey = Identifier(approval, "idempotency_key");
        String(approval, "source_digest", digests.SourceDigest);
        String(approval, "execution_plan_digest", digests.ExecutionPlanDigest);
        var bindingDigest = Digest(approval, "execution_binding_digest");
        String(approval, "expansion_digest", digests.ExpansionDigest);
        String(approval, "effect_manifest_digest", digests.EffectManifestDigest);
        String(approval, "target_refs_digest", digests.TargetRefsDigest);
        String(
            approval,
            "validation_profiles_digest",
            plan.GetProperty("validation_profiles_digest").GetString()!);
        String(
            approval,
            "checkpoint_strategy_digest",
            plan.GetProperty("checkpoint_strategy_digest").GetString()!);
        String(
            approval,
            "hard_budgets_digest",
            plan.GetProperty("hard_budgets_digest").GetString()!);
        var previewId = Identifier(approval, "preview_id");
        var previewDigest = Digest(approval, "preview_digest");
        var expiresAt = Timestamp(approval, "preview_expires_at");
        if (expiresAt <= now)
        {
            throw new ProtocolValidationException(
                "preview_expired",
                "Trusted Phase 8 preview approval has expired.");
        }
        var receiptId = Identifier(approval, "receipt_id");
        if (!receiptId.StartsWith("AUTOCAD_MCP_PHASE8_", StringComparison.Ordinal))
        {
            throw Invalid("Phase 8 receipt ID lacks its canonical namespace.");
        }
        return new(
            intentDigest,
            proofDigest,
            commandId,
            idempotencyKey,
            bindingDigest,
            previewId,
            previewDigest,
            expiresAt,
            receiptId);
    }

    private static int SupportRank(string value) => value switch
    {
        "unsupported" => 0,
        "contract_only" => 1,
        "preview_only" => 2,
        "lab_commit" => 3,
        "certified" => 4,
        _ => -1
    };

    private static JsonElement Object(JsonElement parent, string property)
    {
        var value = parent.GetProperty(property);
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw Invalid($"{property} must be an object.");
        }
        return value;
    }

    private static JsonElement Array(
        JsonElement parent,
        string property,
        int minimum,
        int maximum)
    {
        var value = parent.GetProperty(property);
        if (value.ValueKind != JsonValueKind.Array ||
            value.GetArrayLength() < minimum ||
            value.GetArrayLength() > maximum)
        {
            throw Invalid($"{property} array is outside its bound.");
        }
        return value;
    }

    private static void EnsureExact(
        JsonElement value,
        IReadOnlySet<string> required)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw Invalid("Contract value must be an object.");
        }
        var properties = value.EnumerateObject().ToArray();
        var names = properties.Select(item => item.Name).ToHashSet(StringComparer.Ordinal);
        if (names.Count != properties.Length ||
            names.Count != required.Count ||
            required.Any(item => !names.Contains(item)))
        {
            throw Invalid("Contract object has missing, duplicate, or unknown fields.");
        }
    }

    private static string Identifier(JsonElement parent, string property)
    {
        var value = BoundedString(parent, property, 128);
        if (value.Any(character =>
                !(char.IsAsciiLetterOrDigit(character) ||
                  character is '.' or '_' or '-')))
        {
            throw Invalid($"{property} is not an identifier.");
        }
        return value;
    }

    private static string Digest(JsonElement parent, string property)
    {
        var value = BoundedString(parent, property, 71);
        if (value.Length != 71 ||
            !value.StartsWith("sha256:", StringComparison.Ordinal) ||
            value[7..].Any(character =>
                !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw Invalid($"{property} is not a lowercase SHA-256 digest.");
        }
        return value;
    }

    private static string BoundedString(
        JsonElement parent,
        string property,
        int maximum)
    {
        if (!parent.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.String ||
            string.IsNullOrEmpty(value.GetString()) ||
            value.GetString()!.Length > maximum)
        {
            throw Invalid($"{property} is not a bounded string.");
        }
        return value.GetString()!;
    }

    private static void String(
        JsonElement parent,
        string property,
        string expected)
    {
        var actual = BoundedString(parent, property, Math.Max(256, expected.Length));
        if (!StringComparer.Ordinal.Equals(actual, expected))
        {
            throw Invalid($"{property} differs from the sealed binding.");
        }
    }

    private static void EvidenceString(
        JsonElement parent,
        string property,
        string expected)
    {
        if (!parent.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.String ||
            !StringComparer.Ordinal.Equals(value.GetString(), expected))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Capability evidence differs from sealed or observed Host evidence.");
        }
    }

    private static DateTimeOffset Timestamp(JsonElement parent, string property)
    {
        var value = BoundedString(parent, property, 64);
        if (!DateTimeOffset.TryParseExact(
                value,
                ["O", "yyyy-MM-dd'T'HH:mm:ssK"],
                CultureInfo.InvariantCulture,
                DateTimeStyles.None,
                out var parsed))
        {
            throw Invalid($"{property} must be a timezone-aware timestamp.");
        }
        return parsed;
    }

    private static string DomainHash(string domain, object payload)
    {
        using var document = JsonSerializer.SerializeToDocument(
            new { domain, payload },
            HostProtocol.JsonOptions);
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static IReadOnlySet<string> Set(params string[] values) =>
        new HashSet<string>(values, StringComparer.Ordinal);

    private static ProtocolValidationException Invalid(string message) =>
        new("program_invalid", message);
}

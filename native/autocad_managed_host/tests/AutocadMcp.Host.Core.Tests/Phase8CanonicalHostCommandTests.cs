using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class Phase8CanonicalHostCommandTests
{
    [Fact]
    public void Preview_UsesCanonicalPlanValidatorAndExactTypedCapabilityEvidence()
    {
        using var vector = LoadVector();
        var plan = vector.RootElement.GetProperty("plan");
        var now = DateTimeOffset.UtcNow;
        var arguments = new JsonObject
        {
            ["execution_plan"] = JsonNode.Parse(plan.GetRawText()),
            ["capability_evidence"] = Evidence(plan, Runtime(plan), now, "preview_only")
        };

        using var document = JsonDocument.Parse(arguments.ToJsonString());
        var command = Phase8CanonicalHostCommandParser.Parse(
            "cad.program.preview",
            document.RootElement,
            Runtime(plan),
            now);

        Assert.Equal(
            plan.GetProperty("execution_plan_digest").GetString(),
            command.Digests.ExecutionPlanDigest);
        Assert.Equal(2, command.CapabilityEvidence.Count);
        Assert.Null(command.Approval);
    }

    [Fact]
    public void Parser_RejectsParallelPlanShapeUnknownArgumentsAndRawRestorePayload()
    {
        using var vector = LoadVector();
        var plan = vector.RootElement.GetProperty("plan");
        var now = DateTimeOffset.UtcNow;

        var parallel = new JsonObject
        {
            ["schema_version"] = "cad.execution-plan/1",
            ["source"] = new JsonObject(),
            ["pins"] = new JsonObject(),
            ["operations"] = new JsonArray()
        };
        AssertInvalid(new JsonObject
        {
            ["execution_plan"] = parallel,
            ["capability_evidence"] = new JsonArray()
        }, plan, now);

        var unknown = Arguments(plan, now);
        unknown["execution_binding"] = new JsonObject();
        AssertInvalid(unknown, plan, now);

        var restore = Arguments(plan, now);
        restore["restore_descriptor"] = new JsonObject
        {
            ["entity_type"] = "LINE",
            ["handle"] = "ABC"
        };
        AssertInvalid(restore, plan, now);
    }

    [Fact]
    public void Parser_RejectsForgedExpiredOrUnboundHostCapabilityEvidence()
    {
        using var vector = LoadVector();
        var plan = vector.RootElement.GetProperty("plan");
        var now = DateTimeOffset.UtcNow;
        var arguments = Arguments(plan, now);
        arguments["capability_evidence"]![0]!["host_evidence_digest"] = Digest('9');
        RehashEvidence(arguments["capability_evidence"]![0]!.AsObject());
        AssertInvalid(arguments, plan, now, "capability_missing");

        arguments = Arguments(plan, now);
        arguments["capability_evidence"]![0]!["valid_until"] =
            now.AddMinutes(-1).ToString("O");
        RehashEvidence(arguments["capability_evidence"]![0]!.AsObject());
        AssertInvalid(arguments, plan, now, "capability_missing");
    }

    [Fact]
    public void Parser_AcceptsRfc3339TimestampsWithSixFractionalDigits()
    {
        using var vector = LoadVector();
        var plan = vector.RootElement.GetProperty("plan");
        var now = DateTimeOffset.UtcNow;
        var arguments = Arguments(plan, now);
        var evidence = arguments["capability_evidence"]![0]!.AsObject();
        evidence["issued_at"] = now.AddMinutes(-1)
            .ToString("yyyy-MM-dd'T'HH:mm:ss.ffffffK");
        evidence["valid_until"] = now.AddMinutes(10)
            .ToString("yyyy-MM-dd'T'HH:mm:ss.ffffffK");
        RehashEvidence(evidence);

        using var document = JsonDocument.Parse(arguments.ToJsonString());
        var command = Phase8CanonicalHostCommandParser.Parse(
            "cad.program.preview",
            document.RootElement,
            Runtime(plan),
            now);

        Assert.NotEmpty(command.CapabilityEvidence);
    }

    [Fact]
    public void Commit_RequiresExactCanonicalApprovalAndLabCommitEvidence()
    {
        using var vector = LoadVector();
        var plan = vector.RootElement.GetProperty("plan");
        var now = DateTimeOffset.UtcNow;
        var arguments = Arguments(plan, now, "lab_commit");
        arguments["approval_binding"] = Approval(plan, now);
        using var document = JsonDocument.Parse(arguments.ToJsonString());

        var command = Phase8CanonicalHostCommandParser.Parse(
            "cad.program.commit",
            document.RootElement,
            Runtime(plan),
            now);

        Assert.NotNull(command.Approval);
        Assert.Equal("AUTOCAD_MCP_PHASE8_receipt", command.Approval!.ReceiptId);
        Assert.Matches("^sha256:[0-9a-f]{64}$", command.EffectIdentityDigest());

        arguments["approval_binding"]!["execution_plan_digest"] = Digest('9');
        AssertInvalid(arguments, plan, now);
    }

    [Fact]
    public void CanonicalTargetBoundary_MapsSharedPlanToTypedManagedOperations()
    {
        using var vector = LoadTargetVector();
        var plan = vector.RootElement.GetProperty("execution_plan");
        var now = DateTimeOffset.UtcNow;
        var arguments = Arguments(plan, now);
        using var document = JsonDocument.Parse(arguments.ToJsonString());

        var command = Phase8CanonicalHostCommandParser.Parse(
            "cad.program.preview",
            document.RootElement,
            Runtime(plan),
            now);
        var managed = command.ToManagedPlan();

        Assert.True(command.HasTargetOperations);
        Assert.Collection(
            managed.Operations,
            item => Assert.IsType<CadCopyEntityOperation>(item),
            item => Assert.IsType<CadOffsetEntityOperation>(item),
            item => Assert.IsType<CadMoveEntityOperation>(item));
        Assert.Equal("owner-001", managed.OwnerId);
    }

    [Fact]
    public void CanonicalReceiptAndCheckpoint_RoundTripAndRejectUnknownRestoreData()
    {
        using var vector = LoadVector();
        var plan = vector.RootElement.GetProperty("plan");
        var now = DateTimeOffset.UtcNow;
        var arguments = Arguments(plan, now, "lab_commit");
        arguments["approval_binding"] = Approval(plan, now);
        using var document = JsonDocument.Parse(arguments.ToJsonString());
        var command = Phase8CanonicalHostCommandParser.Parse(
            "cad.program.commit",
            document.RootElement,
            Runtime(plan),
            now);
        var entities = new[]
        {
            new Phase8CanonicalCreatedEntity(
                "line-grid.r000c000",
                "ABC",
                "LINE",
                "PHASE8",
                Digest('e'))
        };
        var checkpoint = Phase8CanonicalCreatedCheckpoint.Create(
            command,
            "43",
            entities,
            now);
        var receipt = Phase8CanonicalCreateReceipt.Create(
            command,
            "43",
            entities,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            now);

        Assert.Equal(
            checkpoint.Serialize(),
            Phase8CanonicalCreatedCheckpoint.Parse(
                checkpoint.Serialize()).Serialize());
        Assert.Equal(
            receipt.Serialize(),
            Phase8CanonicalCreateReceipt.Parse(receipt.Serialize()).Serialize());
        var rollback = Phase8CanonicalRollbackReceipt.Create(
            "AUTOCAD_MCP_PHASE8_RB_receipt",
            checkpoint,
            "rollback-plan-phase8",
            Digest('1'),
            Digest('2'),
            "44",
            now);
        Assert.Equal(
            rollback.Serialize(),
            Phase8CanonicalRollbackReceipt.Parse(rollback.Serialize()).Serialize());

        var injected = JsonNode.Parse(checkpoint.Serialize())!.AsObject();
        injected["restore_descriptor"] = new JsonObject
        {
            ["handle"] = "ABC",
            ["script"] = "_.ERASE"
        };
        var error = Assert.Throws<ProtocolValidationException>(() =>
            Phase8CanonicalCreatedCheckpoint.Parse(injected.ToJsonString()));
        Assert.Equal("ledger_corrupt", error.Code);
    }

    private static JsonObject Arguments(
        JsonElement plan,
        DateTimeOffset now,
        string support = "preview_only") =>
        new()
        {
            ["execution_plan"] = JsonNode.Parse(plan.GetRawText()),
            ["capability_evidence"] =
                Evidence(plan, Runtime(plan), now, support)
        };

    private static JsonArray Evidence(
        JsonElement plan,
        Phase8HostRuntimeEvidence runtime,
        DateTimeOffset now,
        string support)
    {
        var result = new JsonArray();
        var index = 0;
        foreach (var capability in plan.GetProperty("required_capabilities")
                     .EnumerateArray())
        {
            var value = new JsonObject
            {
                ["schema_version"] = "cad.capability-evidence/1",
                ["evidence_id"] = $"evidence-{index}",
                ["evidence_authority"] = "gateway_server",
                ["device_id"] = plan.GetProperty("device_id").GetString(),
                ["capability_key"] = capability.GetString(),
                ["operation_pack"] = "create.core/1",
                ["runtime_id"] = runtime.RuntimeId,
                ["host_family"] = runtime.HostFamily,
                ["entity_type"] = index == 0 ? "LINE" : "CIRCLE",
                ["support_state"] = support,
                ["package_hash"] = runtime.PackageHash,
                ["capability_manifest_hash"] = plan.GetProperty(
                    "execution_pins").GetProperty(
                    "capability_manifest_hash").GetString(),
                ["operation_registry_hash"] = runtime.OperationRegistryHash,
                ["package_signature_verified"] = true,
                ["agent_evidence_digest"] = Digest('7'),
                ["host_evidence_digest"] = runtime.HostEvidenceDigest,
                ["cohort"] = "phase8-lab",
                ["evidence_version"] = "1",
                ["issued_at"] = now.AddMinutes(-1).ToString("O"),
                ["valid_until"] = now.AddMinutes(30).ToString("O"),
                ["evidence_digest"] = Digest('0')
            };
            RehashEvidence(value);
            result.Add(value);
            index++;
        }
        return result;
    }

    private static JsonObject Approval(JsonElement plan, DateTimeOffset now) =>
        new()
        {
            ["schema_version"] = "cad.phase8-approval-binding/1",
            ["action"] = "program_commit",
            ["intent_id"] = "intent-phase8",
            ["consent_id"] = "consent-phase8",
            ["intent_digest"] = Digest('a'),
            ["approval_proof_digest"] = Digest('b'),
            ["device_id"] = plan.GetProperty("device_id").GetString(),
            ["document_id"] = plan.GetProperty("document_id").GetString(),
            ["document_revision"] =
                plan.GetProperty("expected_document_revision").GetString(),
            ["job_id"] = "job-phase8",
            ["command_id"] = "command-phase8",
            ["idempotency_key"] = "idempotency-phase8",
            ["source_digest"] = plan.GetProperty("source_digest").GetString(),
            ["execution_plan_digest"] =
                plan.GetProperty("execution_plan_digest").GetString(),
            ["execution_binding_digest"] = Digest('c'),
            ["expansion_digest"] =
                plan.GetProperty("expansion_digest").GetString(),
            ["effect_manifest_digest"] =
                plan.GetProperty("effect_manifest_digest").GetString(),
            ["target_refs_digest"] =
                plan.GetProperty("target_refs_digest").GetString(),
            ["validation_profiles_digest"] =
                plan.GetProperty("validation_profiles_digest").GetString(),
            ["checkpoint_strategy_digest"] =
                plan.GetProperty("checkpoint_strategy_digest").GetString(),
            ["hard_budgets_digest"] =
                plan.GetProperty("hard_budgets_digest").GetString(),
            ["preview_id"] = "preview-phase8",
            ["preview_digest"] = Digest('d'),
            ["preview_expires_at"] = now.AddMinutes(15).ToString("O"),
            ["receipt_id"] = "AUTOCAD_MCP_PHASE8_receipt"
        };

    private static Phase8HostRuntimeEvidence Runtime(JsonElement plan)
    {
        var pins = plan.GetProperty("execution_pins");
        return new(
            pins.GetProperty("runtime_id").GetString()!,
            pins.GetProperty("runtime_role").GetString()!,
            pins.GetProperty("host_family").GetString()!,
            pins.GetProperty("host_version").GetString()!,
            pins.GetProperty("package_id").GetString()!,
            pins.GetProperty("package_version").GetString()!,
            pins.GetProperty("package_hash").GetString()!,
            pins.GetProperty("operation_registry_version").GetString()!,
            pins.GetProperty("operation_registry_hash").GetString()!,
            Digest('8'));
    }

    private static void RehashEvidence(JsonObject value)
    {
        var canonical = value.DeepClone().AsObject();
        canonical.Remove("evidence_digest");
        using var document = JsonSerializer.SerializeToDocument(
            new
            {
                domain = "cad.capability-evidence/1",
                payload = canonical
            },
            HostProtocol.JsonOptions);
        value["evidence_digest"] =
            $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static void AssertInvalid(
        JsonObject arguments,
        JsonElement plan,
        DateTimeOffset now,
        string code = "program_invalid")
    {
        using var document = JsonDocument.Parse(arguments.ToJsonString());
        var error = Assert.Throws<ProtocolValidationException>(() =>
            Phase8CanonicalHostCommandParser.Parse(
                "cad.program.preview",
                document.RootElement,
                Runtime(plan),
                now));
        Assert.Equal(code, error.Code);
    }

    private static JsonDocument LoadVector()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current is not null &&
               !File.Exists(Path.Combine(current.FullName, "AGENTS.md")))
        {
            current = current.Parent;
        }
        Assert.NotNull(current);
        return JsonDocument.Parse(File.ReadAllText(Path.Combine(
            current!.FullName,
            "packages",
            "host_contracts",
            "program",
            "golden",
            "cad-program-1.0-compiler-vector.json")));
    }

    private static JsonDocument LoadTargetVector()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);
        while (current is not null &&
               !File.Exists(Path.Combine(current.FullName, "AGENTS.md")))
        {
            current = current.Parent;
        }
        Assert.NotNull(current);
        return JsonDocument.Parse(File.ReadAllText(Path.Combine(
            current!.FullName,
            "packages",
            "contracts",
            "fixtures",
            "cad-program-1.0-phase8-target-vector.json")));
    }

    private static string Digest(char value) => $"sha256:{new string(value, 64)}";
}

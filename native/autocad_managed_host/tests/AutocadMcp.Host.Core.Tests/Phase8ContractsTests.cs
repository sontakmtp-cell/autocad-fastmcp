using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class Phase8ContractsTests
{
    [Fact]
    public void PythonGoldenVector_MatchesAllCSharpCanonicalDigests()
    {
        using var vector = LoadVector();
        var source = vector.RootElement.GetProperty("source");
        var plan = vector.RootElement.GetProperty("plan");
        var binding = vector.RootElement.GetProperty("execution_binding");

        var sourceDigest = Phase8ContractValidator.ValidateSource(source);
        var digests = Phase8ContractValidator.ValidateExecutionPlan(plan);
        var bindingDigest = Phase8ContractValidator.ValidateExecutionBinding(binding, plan);

        Assert.Equal(vector.RootElement.GetProperty("source_digest").GetString(), sourceDigest);
        Assert.Equal(sourceDigest, digests.SourceDigest);
        Assert.Equal(
            vector.RootElement.GetProperty("compiler_digest").GetString(),
            digests.CompilerDigest);
        Assert.Equal(
            vector.RootElement.GetProperty("expansion_digest").GetString(),
            digests.ExpansionDigest);
        Assert.Equal(
            vector.RootElement.GetProperty("effect_manifest_digest").GetString(),
            digests.EffectManifestDigest);
        Assert.Equal(
            vector.RootElement.GetProperty("target_refs_digest").GetString(),
            digests.TargetRefsDigest);
        Assert.Equal(
            vector.RootElement.GetProperty("execution_plan_digest").GetString(),
            digests.ExecutionPlanDigest);
        Assert.Equal(
            vector.RootElement.GetProperty("execution_binding_digest").GetString(),
            bindingDigest);
    }

    [Fact]
    public void SourceValidator_IsStrictAndRejectsCodePathAndDynamicExpression()
    {
        using var vector = LoadVector();
        var source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["path"] = @"C:\unsafe.dll";
        AssertInvalid(() => ValidateSource(source));

        source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["operations"]![1]!["end"]!["x"]!["op"] = "eval";
        AssertInvalid(() => ValidateSource(source));

        source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["operations"]![1]!["kind"] = "delete";
        AssertInvalid(() => ValidateSource(source));
    }

    [Fact]
    public void PlanValidator_RejectsTamperingModifyEffectsAndUnknownOperations()
    {
        using var vector = LoadVector();
        var plan = JsonNode.Parse(
            vector.RootElement.GetProperty("plan").GetRawText())!.AsObject();
        plan["operations"]![1]!["end"]!["x_mm"] = "999";
        AssertInvalid(() => ValidatePlan(plan));

        plan = JsonNode.Parse(
            vector.RootElement.GetProperty("plan").GetRawText())!.AsObject();
        plan["effect_manifest"]!["modifies"] = 1;
        AssertInvalid(() => ValidatePlan(plan));

        plan = JsonNode.Parse(
            vector.RootElement.GetProperty("plan").GetRawText())!.AsObject();
        plan["operations"]![1]!["kind"] = "trim";
        AssertInvalid(() => ValidatePlan(plan));

        plan = JsonNode.Parse(
            vector.RootElement.GetProperty("plan").GetRawText())!.AsObject();
        plan["command"] = "_.ERASE ALL";
        AssertInvalid(() => ValidatePlan(plan));
    }

    [Fact]
    public void Contracts_RejectUnicodeNumericUnknownFieldAndRefAuthorityMutations()
    {
        using var vector = LoadVector();
        var source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["operations"]![3]!["text"] = "Phase 8 — ban ve Δ";
        AssertInvalid(() => ValidateSource(source));

        source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["variables"]![0]!["value"]!["value"] = 1.0;
        AssertInvalid(() => ValidateSource(source));

        source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["artifact_refs"] = new JsonArray
        {
            new JsonObject
            {
                ["artifact_id"] = "artifact-program-input",
                ["owner_id"] = "owner-001",
                ["content_type"] = "application/vnd.autocad-mcp.cad-program+json",
                ["byte_length"] = 128,
                ["artifact_digest"] = Digest('4'),
                ["path"] = @"C:\payload.json"
            }
        };
        AssertInvalid(() => ValidateSource(source));

        source = JsonNode.Parse(
            vector.RootElement.GetProperty("source").GetRawText())!.AsObject();
        source["component_refs"] = new JsonArray
        {
            new JsonObject
            {
                ["component_id"] = "component-grid",
                ["owner_id"] = "owner-001",
                ["component_version"] = "1.0.0",
                ["content_type"] = "application/vnd.autocad-mcp.component+json",
                ["byte_length"] = 128,
                ["component_digest"] = Digest('5'),
                ["url"] = "https://example.invalid/component"
            }
        };
        AssertInvalid(() => ValidateSource(source));

        var plan = JsonNode.Parse(
            vector.RootElement.GetProperty("plan").GetRawText())!.AsObject();
        plan["operations"]![1]!["end"]!["x_mm"] = "25.400";
        AssertInvalid(() => ValidatePlan(plan));

        plan = JsonNode.Parse(
            vector.RootElement.GetProperty("plan").GetRawText())!.AsObject();
        var binding = JsonNode.Parse(
            vector.RootElement.GetProperty("execution_binding").GetRawText())!.AsObject();
        binding["source_digest"] = Digest('9');
        AssertInvalid(() => ValidateBinding(binding, plan));
    }

    [Fact]
    public void HostContract_ExposesVerificationOnlyAndKeepsCreateOnlyRegistry()
    {
        Assert.Equal("cad.program/1.0", Phase8Contract.SourceSchemaVersion);
        Assert.Equal("cad.execution-plan/1", Phase8Contract.PlanSchemaVersion);
        Assert.Equal(7, Phase8Contract.CreateOnlyKinds.Count);
        Assert.DoesNotContain("delete", Phase8Contract.CreateOnlyKinds);
        Assert.DoesNotContain("trim", Phase8Contract.CreateOnlyKinds);
        Assert.DoesNotContain("fillet", Phase8Contract.CreateOnlyKinds);
        Assert.DoesNotContain("chamfer", Phase8Contract.CreateOnlyKinds);
        Assert.DoesNotContain(
            typeof(Phase8ContractValidator).GetMethods(),
            method => method.Name.Contains("Compile", StringComparison.Ordinal));
    }

    private static void ValidateSource(JsonObject source)
    {
        using var document = JsonDocument.Parse(source.ToJsonString());
        Phase8ContractValidator.ValidateSource(document.RootElement);
    }

    private static void ValidatePlan(JsonObject plan)
    {
        using var document = JsonDocument.Parse(plan.ToJsonString());
        Phase8ContractValidator.ValidateExecutionPlan(document.RootElement);
    }

    private static void ValidateBinding(JsonObject binding, JsonObject plan)
    {
        using var bindingDocument = JsonDocument.Parse(binding.ToJsonString());
        using var planDocument = JsonDocument.Parse(plan.ToJsonString());
        Phase8ContractValidator.ValidateExecutionBinding(
            bindingDocument.RootElement,
            planDocument.RootElement);
    }

    private static void AssertInvalid(Action action)
    {
        var error = Assert.Throws<ProtocolValidationException>(action);
        Assert.Equal("program_invalid", error.Code);
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
        var path = Path.Combine(
            current!.FullName,
            "packages",
            "host_contracts",
            "program",
            "golden",
            "cad-program-1.0-compiler-vector.json");
        return JsonDocument.Parse(File.ReadAllText(path));
    }

    private static string Digest(char value) => $"sha256:{new string(value, 64)}";
}

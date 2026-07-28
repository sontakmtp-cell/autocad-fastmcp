using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace AutocadMcp.Host.Core;

public static class Phase8Contract
{
    public const string SourceSchemaVersion = "cad.program/1.0";
    public const string RegistryVersion = "cad.program/1.0-create-core";
    public const string Phase8RegistryVersion = "cad.program/1.0-phase8-core";
    public const string PlanSchemaVersion = "cad.execution-plan/1";
    public const string EffectSchemaVersion = "cad.effect-manifest/1";
    public const string CompilerId = "autocad-mcp.gateway.cad-program-v1";
    public const string CompilerVersion = "1.1.0";
    public const string CompilerDigest =
        "sha256:98732381c0674120be01bdae9b6e825a89dd2579a6c6af31e8ec4030bf1c35d9";
    public const string SourceDigestDomain = "cad.program.source/1";
    public const string CompilerDigestDomain = "cad.program.compiler/1";
    public const string ExpansionDigestDomain = "cad.program.expansion/1";
    public const string EffectDigestDomain = "cad.effect-manifest/1";
    public const string TargetRefsDigestDomain = "cad.target-refs/1";
    public const string ValidationProfilesDigestDomain = "cad.validation-profiles/1";
    public const string CheckpointStrategyDigestDomain = "cad.checkpoint-strategy/1";
    public const string HardBudgetsDigestDomain = "cad.execution-budgets/1";
    public const string PlanDigestDomain = "cad.execution-plan/1";
    public const string ExecutionBindingDigestDomain = "cad.execution-binding/1";
    public const int MaxExpressionDepth = 16;
    public const int MaxExpressionNodes = 1024;
    public const int MaxExpandedOperations = 1024;

    public static readonly IReadOnlySet<string> CreateOnlyKinds = new HashSet<string>(
        [
            "ensure_layer",
            "create_line",
            "create_circle",
            "create_polyline",
            "create_rectangle",
            "create_text",
            "create_dimension_linear"
        ],
        StringComparer.Ordinal);

    public static readonly IReadOnlySet<string> TargetKinds = new HashSet<string>(
        ["copy_entity", "offset_entity", "move_entity"],
        StringComparer.Ordinal);

    public static bool IsAllowedKind(string kind) =>
        CreateOnlyKinds.Contains(kind) || TargetKinds.Contains(kind);

    public static bool IsAllowedRegistry(string registry) =>
        registry is RegistryVersion or Phase8RegistryVersion;
}

public sealed record Phase8PlanDigests(
    string SourceDigest,
    string CompilerDigest,
    string ExpansionDigest,
    string EffectManifestDigest,
    string TargetRefsDigest,
    string ExecutionPlanDigest);

/// <summary>
/// Compile-boundary validation only.  The Host verifies a sealed plan and never
/// evaluates expressions or expands repeats.
/// </summary>
public static class Phase8ContractValidator
{
    private static readonly Regex Identifier = new(
        "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
        RegexOptions.CultureInvariant);
    private static readonly Regex Digest = new(
        "^sha256:[0-9a-f]{64}$",
        RegexOptions.CultureInvariant);
    private static readonly Regex DecimalText = new(
        "^-?(?:0|[1-9][0-9]{0,18})(?:\\.[0-9]{1,18})?$",
        RegexOptions.CultureInvariant);
    private static readonly Regex CanonicalDecimalText = new(
        "^-?(?:0|[1-9][0-9]{0,18})(?:\\.[0-9]{0,17}[1-9])?$",
        RegexOptions.CultureInvariant);

    public static string ValidateSource(JsonElement source)
    {
        RequireObject(source);
        EnsureExact(
            source,
            [
                "schema_version", "registry_version", "program_id", "program_revision",
                "device_id", "source_snapshot_id", "document_id",
                "expected_document_revision", "variables", "operations", "budgets",
                "required_capabilities", "validation_profiles", "artifact_refs",
                "component_refs", "semantic_digest"
            ],
            ["parent_revision"]);
        RequireString(source, "schema_version", Phase8Contract.SourceSchemaVersion);
        var registryVersion = RequireBoundedString(source, "registry_version", 256);
        if (!Phase8Contract.IsAllowedRegistry(registryVersion))
        {
            throw Invalid("Source registry version is not allowlisted.");
        }
        RequireIdentifier(source, "program_id");
        RequirePositiveInteger(source, "program_revision");
        RequireIdentifier(source, "device_id");
        RequireIdentifier(source, "source_snapshot_id");
        RequireIdentifier(source, "document_id");
        RequireBoundedString(source, "expected_document_revision", 256);

        var variables = RequireArray(source, "variables", 0, 64);
        var variableNames = new HashSet<string>(StringComparer.Ordinal);
        foreach (var variable in variables.EnumerateArray())
        {
            EnsureExact(variable, ["name", "value"]);
            var name = RequireIdentifier(variable, "name");
            if (!variableNames.Add(name))
            {
                throw Invalid("Variable names must be unique.");
            }
            ValidateNumericValue(variable.GetProperty("value"));
        }

        var operations = RequireArray(source, "operations", 1, 256);
        var operationIds = new HashSet<string>(StringComparer.Ordinal);
        var nodeCount = 0;
        foreach (var operation in operations.EnumerateArray())
        {
            ValidateSourceOperation(operation, operationIds, ref nodeCount);
        }
        if (operations.EnumerateArray().Any(
                item => Phase8Contract.TargetKinds.Contains(
                    item.GetProperty("kind").GetString()!)) &&
            registryVersion != Phase8Contract.Phase8RegistryVersion)
        {
            throw Invalid("Target operations require the Phase 8 core registry.");
        }
        if (nodeCount > Phase8Contract.MaxExpressionNodes)
        {
            throw Invalid("Expression node budget exceeded.");
        }
        ValidateIdentifierArray(source, "required_capabilities", 64);
        ValidateIdentifierArray(source, "validation_profiles", 16, requireOne: true);
        ValidateOpaqueRefs(source, "artifact_refs", "artifact_id", "artifact_digest");
        ValidateOpaqueRefs(
            source,
            "component_refs",
            "component_id",
            "component_digest",
            "component_version");
        ValidateBudgets(source.GetProperty("budgets"));

        var claimed = RequireDigest(source, "semantic_digest");
        var actual = HashWithoutProperty(
            source,
            "semantic_digest",
            Phase8Contract.SourceDigestDomain);
        if (!StringComparer.Ordinal.Equals(claimed, actual))
        {
            throw Invalid("Source semantic digest mismatch.");
        }
        return actual;
    }

    public static Phase8PlanDigests ValidateExecutionPlan(JsonElement plan)
    {
        RequireObject(plan);
        EnsureExact(
            plan,
            [
                "schema_version", "plan_id", "source_schema_version",
                "source_registry_version",
                "source_program_id", "source_program_revision", "source_digest",
                "compiler", "device_id", "document_id", "source_snapshot_id",
                "expected_document_revision", "operations", "expansion_digest",
                "effect_manifest", "effect_manifest_digest", "materialized_target_refs",
                "target_refs_digest", "validation_profiles_digest",
                "checkpoint_strategy_digest", "hard_budgets_digest",
                "execution_pins", "budgets",
                "required_capabilities", "validation_profiles", "artifact_refs",
                "component_refs", "checkpoint_strategy", "execution_plan_digest"
            ]);
        RequireString(plan, "schema_version", Phase8Contract.PlanSchemaVersion);
        RequireString(plan, "source_schema_version", Phase8Contract.SourceSchemaVersion);
        var sourceRegistryVersion =
            RequireBoundedString(plan, "source_registry_version", 256);
        if (!Phase8Contract.IsAllowedRegistry(sourceRegistryVersion))
        {
            throw Invalid("Plan source registry version is not allowlisted.");
        }
        RequireIdentifier(plan, "plan_id");
        RequireIdentifier(plan, "source_program_id");
        RequirePositiveInteger(plan, "source_program_revision");
        var sourceDigest = RequireDigest(plan, "source_digest");
        RequireIdentifier(plan, "device_id");
        RequireIdentifier(plan, "document_id");
        RequireIdentifier(plan, "source_snapshot_id");
        RequireBoundedString(plan, "expected_document_revision", 256);
        var checkpointStrategy =
            RequireBoundedString(plan, "checkpoint_strategy", 64);
        if (checkpointStrategy is not (
            "cad.rollback.checkpoint/1-created-entities" or
            "cad.rollback.checkpoint/2"))
        {
            throw Invalid("Checkpoint strategy is not allowlisted.");
        }

        var compiler = plan.GetProperty("compiler");
        EnsureExact(
            compiler,
            ["compiler_id", "compiler_version", "compiler_digest", "compiler_package_hash"]);
        RequireString(compiler, "compiler_id", Phase8Contract.CompilerId);
        RequireString(compiler, "compiler_version", Phase8Contract.CompilerVersion);
        var compilerDigest = RequireDigest(compiler, "compiler_digest");
        if (!StringComparer.Ordinal.Equals(compilerDigest, Phase8Contract.CompilerDigest))
        {
            throw Invalid("Compiler digest mismatch.");
        }
        RequireDigest(compiler, "compiler_package_hash");

        var operations = RequireArray(
            plan,
            "operations",
            1,
            Phase8Contract.MaxExpandedOperations);
        var operationIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var operation in operations.EnumerateArray())
        {
            ValidateConcreteOperation(operation, operationIds);
        }
        var expansionDigest = RequireDigest(plan, "expansion_digest");
        var actualExpansion = HashWrappedArray(
            "operations",
            operations,
            Phase8Contract.ExpansionDigestDomain);
        if (!StringComparer.Ordinal.Equals(expansionDigest, actualExpansion))
        {
            throw Invalid("Expansion digest mismatch.");
        }

        var effect = plan.GetProperty("effect_manifest");
        var effectDigest = RequireDigest(plan, "effect_manifest_digest");
        var actualEffect = HashDomain(Phase8Contract.EffectDigestDomain, effect);
        if (!StringComparer.Ordinal.Equals(effectDigest, actualEffect))
        {
            throw Invalid("Effect manifest digest mismatch.");
        }

        var targetRefs = RequireArray(
            plan,
            "materialized_target_refs",
            0,
            Phase8Contract.MaxExpandedOperations);
        ValidateTargetRefs(targetRefs);
        ValidateTargetClosure(plan, operations, targetRefs, sourceRegistryVersion);
        ValidateEffectManifest(effect, operations, targetRefs);
        if (!StringComparer.Ordinal.Equals(
                checkpointStrategy,
                effect.GetProperty("checkpoint_strategy").GetString()))
        {
            throw Invalid(
                "Plan checkpoint strategy does not match effect manifest.");
        }
        var targetRefsDigest = RequireDigest(plan, "target_refs_digest");
        var actualTargetRefs = HashWrappedArray(
            "target_refs",
            targetRefs,
            Phase8Contract.TargetRefsDigestDomain);
        if (!StringComparer.Ordinal.Equals(targetRefsDigest, actualTargetRefs))
        {
            throw Invalid("Target refs digest mismatch.");
        }
        var profilesDigest = RequireDigest(plan, "validation_profiles_digest");
        var actualProfiles = HashWrappedArray(
            "validation_profiles",
            plan.GetProperty("validation_profiles"),
            Phase8Contract.ValidationProfilesDigestDomain);
        if (!StringComparer.Ordinal.Equals(profilesDigest, actualProfiles))
        {
            throw Invalid("Validation profiles digest mismatch.");
        }
        var checkpointDigest = RequireDigest(plan, "checkpoint_strategy_digest");
        var actualCheckpoint = HashWrappedString(
            "checkpoint_strategy",
            plan.GetProperty("checkpoint_strategy").GetString()!,
            Phase8Contract.CheckpointStrategyDigestDomain);
        if (!StringComparer.Ordinal.Equals(checkpointDigest, actualCheckpoint))
        {
            throw Invalid("Checkpoint strategy digest mismatch.");
        }
        var hardBudgetsDigest = RequireDigest(plan, "hard_budgets_digest");
        var actualHardBudgets = HashHardBudgets(plan.GetProperty("budgets"));
        if (!StringComparer.Ordinal.Equals(hardBudgetsDigest, actualHardBudgets))
        {
            throw Invalid("Hard budgets digest mismatch.");
        }
        ValidateExecutionPins(plan.GetProperty("execution_pins"));
        ValidatePlanBudgets(plan.GetProperty("budgets"), operations);
        ValidateIdentifierArray(plan, "required_capabilities", 64);
        ValidateIdentifierArray(plan, "validation_profiles", 16, requireOne: true);
        ValidateOpaqueRefs(plan, "artifact_refs", "artifact_id", "artifact_digest");
        ValidateOpaqueRefs(
            plan,
            "component_refs",
            "component_id",
            "component_digest",
            "component_version");

        var planDigest = RequireDigest(plan, "execution_plan_digest");
        var actualPlan = HashWithoutProperty(
            plan,
            "execution_plan_digest",
            Phase8Contract.PlanDigestDomain);
        if (!StringComparer.Ordinal.Equals(planDigest, actualPlan))
        {
            throw Invalid("Execution plan digest mismatch.");
        }
        return new(
            sourceDigest,
            compilerDigest,
            expansionDigest,
            effectDigest,
            targetRefsDigest,
            planDigest);
    }

    public static string ValidateExecutionBinding(JsonElement binding, JsonElement plan)
    {
        ValidateExecutionPlan(plan);
        EnsureExact(
            binding,
            [
                "schema_version", "action", "source_schema_version", "source_program_id",
                "source_registry_version",
                "source_program_revision", "source_digest", "compiler_id",
                "compiler_version", "compiler_digest", "compiler_package_hash",
                "plan_schema_version", "execution_plan_digest", "expansion_digest",
                "effect_manifest_digest", "target_refs_digest",
                "validation_profiles_digest", "checkpoint_strategy_digest",
                "hard_budgets_digest", "runtime_id", "runtime_role", "host_family",
                "host_version", "package_id", "package_version", "package_hash",
                "capability_manifest_hash", "operation_registry_version",
                "operation_registry_hash", "policy_version", "rollout_policy_digest",
                "device_id", "document_id", "source_snapshot_id", "document_revision",
                "execution_binding_digest"
            ],
            ["preview_id", "preview_expires_at", "receipt_id"]);
        RequireString(binding, "schema_version", "cad.execution-binding/1");
        var action = RequireBoundedString(binding, "action", 16);
        var hasPreview = binding.TryGetProperty("preview_id", out _);
        var hasExpiry = binding.TryGetProperty("preview_expires_at", out _);
        var hasReceipt = binding.TryGetProperty("receipt_id", out _);
        if (action == "compile_only" && (hasPreview || hasExpiry || hasReceipt) ||
            action == "preview" && (!hasPreview || !hasExpiry || hasReceipt) ||
            action == "commit" && (!hasPreview || !hasExpiry || !hasReceipt) ||
            action is not ("compile_only" or "preview" or "commit"))
        {
            throw Invalid("Execution action fields do not match.");
        }

        RequireSame(binding, "source_schema_version", plan, "source_schema_version");
        RequireSame(binding, "source_registry_version", plan, "source_registry_version");
        RequireSame(binding, "source_program_id", plan, "source_program_id");
        if (binding.GetProperty("source_program_revision").GetInt32() !=
            plan.GetProperty("source_program_revision").GetInt32())
        {
            throw Invalid("Execution binding source revision mismatch.");
        }
        RequireSame(binding, "source_digest", plan, "source_digest");
        var compiler = plan.GetProperty("compiler");
        RequireSame(binding, "compiler_id", compiler, "compiler_id");
        RequireSame(binding, "compiler_version", compiler, "compiler_version");
        RequireSame(binding, "compiler_digest", compiler, "compiler_digest");
        RequireSame(binding, "compiler_package_hash", compiler, "compiler_package_hash");
        RequireSame(binding, "plan_schema_version", plan, "schema_version");
        foreach (var field in new[]
        {
            "execution_plan_digest", "expansion_digest", "effect_manifest_digest",
            "target_refs_digest", "validation_profiles_digest",
            "checkpoint_strategy_digest", "hard_budgets_digest"
        })
        {
            RequireSame(binding, field, plan, field);
        }
        var pins = plan.GetProperty("execution_pins");
        foreach (var field in new[]
        {
            "runtime_id", "runtime_role", "host_family", "host_version", "package_id",
            "package_version", "package_hash", "capability_manifest_hash",
            "operation_registry_version", "operation_registry_hash", "policy_version",
            "rollout_policy_digest"
        })
        {
            RequireSame(binding, field, pins, field);
        }
        RequireSame(binding, "device_id", plan, "device_id");
        RequireSame(binding, "document_id", plan, "document_id");
        RequireSame(binding, "source_snapshot_id", plan, "source_snapshot_id");
        RequireSame(binding, "document_revision", plan, "expected_document_revision");

        var claimed = RequireDigest(binding, "execution_binding_digest");
        var actual = HashWithoutProperty(
            binding,
            "execution_binding_digest",
            Phase8Contract.ExecutionBindingDigestDomain);
        if (!StringComparer.Ordinal.Equals(claimed, actual))
        {
            throw Invalid("Execution binding digest mismatch.");
        }
        return actual;
    }

    private static void ValidateSourceOperation(
        JsonElement operation,
        HashSet<string> operationIds,
        ref int nodeCount)
    {
        RequireObject(operation);
        var kind = RequireBoundedString(operation, "kind", 64);
        if (!Phase8Contract.IsAllowedKind(kind))
        {
            throw Invalid("Operation is outside the allowlisted registry.");
        }
        var operationId = RequireIdentifier(operation, "operation_id");
        if (!operationIds.Add(operationId))
        {
            throw Invalid("Operation IDs must be unique.");
        }
        var common = new[] { "kind", "operation_id" };
        var optional = new[] { "repeat" };
        switch (kind)
        {
            case "ensure_layer":
                EnsureExact(operation, [.. common, "name"], ["color_index"]);
                RequireBoundedString(operation, "name", 255);
                break;
            case "create_line":
                EnsureExact(operation, [.. common, "layer", "start", "end"], optional);
                ValidateLayer(operation.GetProperty("layer"));
                ValidatePoint(operation.GetProperty("start"), ref nodeCount);
                ValidatePoint(operation.GetProperty("end"), ref nodeCount);
                break;
            case "create_circle":
                EnsureExact(operation, [.. common, "layer", "center", "radius"], optional);
                ValidateLayer(operation.GetProperty("layer"));
                ValidatePoint(operation.GetProperty("center"), ref nodeCount);
                ValidateExpression(operation.GetProperty("radius"), 1, ref nodeCount);
                break;
            case "create_polyline":
                EnsureExact(
                    operation,
                    [.. common, "layer", "vertices"],
                    [.. optional, "closed"]);
                ValidateLayer(operation.GetProperty("layer"));
                foreach (var point in RequireArray(operation, "vertices", 2, 4096).EnumerateArray())
                {
                    ValidatePoint(point, ref nodeCount);
                }
                break;
            case "create_rectangle":
                EnsureExact(
                    operation,
                    [.. common, "layer", "first_corner", "opposite_corner"],
                    optional);
                ValidateLayer(operation.GetProperty("layer"));
                ValidatePoint(operation.GetProperty("first_corner"), ref nodeCount);
                ValidatePoint(operation.GetProperty("opposite_corner"), ref nodeCount);
                break;
            case "create_text":
                EnsureExact(
                    operation,
                    [.. common, "layer", "position", "text", "height", "rotation"],
                    optional);
                ValidateLayer(operation.GetProperty("layer"));
                ValidatePoint(operation.GetProperty("position"), ref nodeCount);
                RequireBoundedString(operation, "text", 65_536);
                ValidateExpression(operation.GetProperty("height"), 1, ref nodeCount);
                ValidateExpression(operation.GetProperty("rotation"), 1, ref nodeCount);
                break;
            case "create_dimension_linear":
                EnsureExact(
                    operation,
                    [
                        .. common, "layer", "extension_line1_point",
                        "extension_line2_point", "dimension_line_point"
                    ],
                    [.. optional, "text_override"]);
                ValidateLayer(operation.GetProperty("layer"));
                ValidatePoint(operation.GetProperty("extension_line1_point"), ref nodeCount);
                ValidatePoint(operation.GetProperty("extension_line2_point"), ref nodeCount);
                ValidatePoint(operation.GetProperty("dimension_line_point"), ref nodeCount);
                break;
            case "copy_entity":
            case "move_entity":
                EnsureExact(
                    operation,
                    [.. common, "target_ref_id", "displacement"],
                    optional);
                RequireIdentifier(operation, "target_ref_id");
                ValidatePoint(operation.GetProperty("displacement"), ref nodeCount);
                break;
            default:
                EnsureExact(
                    operation,
                    [.. common, "target_ref_id", "signed_distance"],
                    optional);
                RequireIdentifier(operation, "target_ref_id");
                ValidateExpression(
                    operation.GetProperty("signed_distance"),
                    1,
                    ref nodeCount);
                break;
        }
        if (operation.TryGetProperty("repeat", out var repeat))
        {
            if (kind == "ensure_layer")
            {
                throw Invalid("ensure_layer cannot be repeated.");
            }
            ValidateRepeat(repeat, ref nodeCount);
        }
    }

    private static void ValidateRepeat(JsonElement repeat, ref int nodeCount)
    {
        var kind = RequireBoundedString(repeat, "kind", 32);
        switch (kind)
        {
            case "linear":
                EnsureExact(repeat, ["kind", "count", "offset"]);
                ValidateExpression(repeat.GetProperty("count"), 1, ref nodeCount);
                ValidatePoint(repeat.GetProperty("offset"), ref nodeCount);
                break;
            case "rectangular":
                EnsureExact(
                    repeat,
                    ["kind", "rows", "columns", "row_offset", "column_offset"]);
                ValidateExpression(repeat.GetProperty("rows"), 1, ref nodeCount);
                ValidateExpression(repeat.GetProperty("columns"), 1, ref nodeCount);
                ValidatePoint(repeat.GetProperty("row_offset"), ref nodeCount);
                ValidatePoint(repeat.GetProperty("column_offset"), ref nodeCount);
                break;
            case "polar":
                EnsureExact(repeat, ["kind", "count", "center", "total_angle"]);
                ValidateExpression(repeat.GetProperty("count"), 1, ref nodeCount);
                ValidatePoint(repeat.GetProperty("center"), ref nodeCount);
                ValidateExpression(repeat.GetProperty("total_angle"), 1, ref nodeCount);
                break;
            default:
                throw Invalid("Repeat kind is not allowlisted.");
        }
    }

    private static void ValidateExpression(JsonElement expression, int depth, ref int nodes)
    {
        if (depth > Phase8Contract.MaxExpressionDepth)
        {
            throw Invalid("Expression depth exceeded.");
        }
        nodes++;
        var op = RequireBoundedString(expression, "op", 16);
        switch (op)
        {
            case "literal":
                EnsureExact(expression, ["op", "value"]);
                ValidateNumericValue(expression.GetProperty("value"));
                break;
            case "variable":
                EnsureExact(expression, ["op", "name"]);
                RequireIdentifier(expression, "name");
                break;
            case "index":
                EnsureExact(expression, ["op"]);
                break;
            case "neg":
            case "abs":
                EnsureExact(expression, ["op", "operand"]);
                ValidateExpression(expression.GetProperty("operand"), depth + 1, ref nodes);
                break;
            case "add":
            case "sub":
            case "mul":
            case "div":
                EnsureExact(expression, ["op", "left", "right"]);
                ValidateExpression(expression.GetProperty("left"), depth + 1, ref nodes);
                ValidateExpression(expression.GetProperty("right"), depth + 1, ref nodes);
                break;
            case "min":
            case "max":
                EnsureExact(expression, ["op", "arguments"]);
                foreach (var item in RequireArray(expression, "arguments", 2, 8).EnumerateArray())
                {
                    ValidateExpression(item, depth + 1, ref nodes);
                }
                break;
            case "convert":
                EnsureExact(expression, ["op", "operand", "unit"]);
                ValidateExpression(expression.GetProperty("operand"), depth + 1, ref nodes);
                RequireBoundedString(expression, "unit", 3);
                break;
            default:
                throw Invalid("Expression operator is not allowlisted.");
        }
    }

    private static void ValidateNumericValue(JsonElement value)
    {
        EnsureExact(value, ["type", "value"], ["unit"]);
        var type = RequireBoundedString(value, "type", 16);
        if (type is not ("integer" or "scalar" or "length" or "angle"))
        {
            throw Invalid("Numeric type is not allowlisted.");
        }
        var text = RequireBoundedString(value, "value", 40);
        if (!DecimalText.IsMatch(text) ||
            !decimal.TryParse(text, NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
                CultureInfo.InvariantCulture, out var parsed))
        {
            throw Invalid("Numeric value is not a canonical decimal.");
        }
        if (parsed == 0 && text.StartsWith("-", StringComparison.Ordinal))
        {
            throw Invalid("Negative zero is not canonical.");
        }
        if (type == "integer" && parsed != decimal.Truncate(parsed))
        {
            throw Invalid("Integer numeric value must be integral.");
        }
        var hasUnit = value.TryGetProperty("unit", out var unitElement);
        var unit = hasUnit ? unitElement.GetString() : null;
        if (type == "length" && unit is not ("mm" or "cm" or "m" or "in" or "ft") ||
            type == "angle" && unit is not ("rad" or "deg") ||
            type is "integer" or "scalar" && hasUnit)
        {
            throw Invalid("Numeric unit does not match type.");
        }
    }

    private static void ValidatePoint(JsonElement point, ref int nodeCount)
    {
        EnsureExact(point, ["x", "y", "z"]);
        ValidateExpression(point.GetProperty("x"), 1, ref nodeCount);
        ValidateExpression(point.GetProperty("y"), 1, ref nodeCount);
        ValidateExpression(point.GetProperty("z"), 1, ref nodeCount);
    }

    private static void ValidateLayer(JsonElement layer)
    {
        if (layer.ValueKind == JsonValueKind.String)
        {
            if (string.IsNullOrWhiteSpace(layer.GetString()) || layer.GetString()!.Length > 255)
            {
                throw Invalid("Layer name is invalid.");
            }
            return;
        }
        EnsureExact(layer, ["operation_id", "output"]);
        RequireIdentifier(layer, "operation_id");
        RequireString(layer, "output", "layer");
    }

    private static void ValidateConcreteOperation(
        JsonElement operation,
        HashSet<string> operationIds)
    {
        var kind = RequireBoundedString(operation, "kind", 64);
        if (!Phase8Contract.IsAllowedKind(kind))
        {
            throw Invalid("Sealed plan operation is outside the allowlisted registry.");
        }
        RequireInteger(operation, "operation_version", 1);
        var id = RequireIdentifier(operation, "operation_id");
        RequireIdentifier(operation, "source_operation_id");
        if (!operationIds.Add(id))
        {
            throw Invalid("Expanded operation IDs must be unique.");
        }
        var common = new[] { "kind", "operation_version", "operation_id", "source_operation_id" };
        switch (kind)
        {
            case "ensure_layer":
                EnsureExact(operation, [.. common, "name"], ["color_index"]);
                RequireBoundedString(operation, "name", 255);
                break;
            case "create_line":
                EnsureExact(operation, [.. common, "layer", "start", "end"]);
                RequireBoundedString(operation, "layer", 255);
                ValidateConcretePoint(operation.GetProperty("start"));
                ValidateConcretePoint(operation.GetProperty("end"));
                break;
            case "create_circle":
                EnsureExact(operation, [.. common, "layer", "center", "radius_mm"]);
                RequireBoundedString(operation, "layer", 255);
                ValidateConcretePoint(operation.GetProperty("center"));
                RequireCanonicalDecimal(operation, "radius_mm", positive: true);
                break;
            case "create_polyline":
                EnsureExact(operation, [.. common, "layer", "vertices", "closed"]);
                RequireBoundedString(operation, "layer", 255);
                foreach (var point in RequireArray(operation, "vertices", 2, 4096).EnumerateArray())
                {
                    ValidateConcretePoint(point);
                }
                if (operation.GetProperty("closed").ValueKind is not (
                    JsonValueKind.True or JsonValueKind.False))
                {
                    throw Invalid("closed must be boolean.");
                }
                break;
            case "create_rectangle":
                EnsureExact(operation, [.. common, "layer", "first_corner", "opposite_corner"]);
                RequireBoundedString(operation, "layer", 255);
                ValidateConcretePoint(operation.GetProperty("first_corner"));
                ValidateConcretePoint(operation.GetProperty("opposite_corner"));
                break;
            case "create_text":
                EnsureExact(
                    operation,
                    [.. common, "layer", "position", "text", "height_mm", "rotation_rad"]);
                RequireBoundedString(operation, "layer", 255);
                ValidateConcretePoint(operation.GetProperty("position"));
                RequireBoundedString(operation, "text", 65_536);
                RequireCanonicalDecimal(operation, "height_mm", positive: true);
                RequireCanonicalDecimal(operation, "rotation_rad");
                break;
            case "create_dimension_linear":
                EnsureExact(
                    operation,
                    [
                        .. common, "layer", "extension_line1_point",
                        "extension_line2_point", "dimension_line_point"
                    ],
                    ["text_override"]);
                RequireBoundedString(operation, "layer", 255);
                ValidateConcretePoint(operation.GetProperty("extension_line1_point"));
                ValidateConcretePoint(operation.GetProperty("extension_line2_point"));
                ValidateConcretePoint(operation.GetProperty("dimension_line_point"));
                break;
            case "copy_entity":
                EnsureExact(
                    operation,
                    [.. common, "target_ref_id", "displacement_mm", "output_id"]);
                RequireIdentifier(operation, "target_ref_id");
                ValidateConcreteVector(
                    operation.GetProperty("displacement_mm"),
                    nonZero: true);
                RequireIdentifier(operation, "output_id");
                break;
            case "offset_entity":
                EnsureExact(
                    operation,
                    [.. common, "target_ref_id", "signed_distance_mm", "output_id"]);
                RequireIdentifier(operation, "target_ref_id");
                if (RequireCanonicalDecimal(operation, "signed_distance_mm") == 0)
                {
                    throw Invalid("signed_distance_mm must be non-zero.");
                }
                RequireIdentifier(operation, "output_id");
                break;
            default:
                EnsureExact(
                    operation,
                    [.. common, "target_ref_id", "displacement_mm"]);
                RequireIdentifier(operation, "target_ref_id");
                ValidateConcreteVector(
                    operation.GetProperty("displacement_mm"),
                    nonZero: true);
                break;
        }
    }

    private static void ValidateConcretePoint(JsonElement point)
    {
        EnsureExact(point, ["x_mm", "y_mm", "z_mm"]);
        RequireCanonicalDecimal(point, "x_mm");
        RequireCanonicalDecimal(point, "y_mm");
        RequireCanonicalDecimal(point, "z_mm");
    }

    private static void ValidateConcreteVector(JsonElement vector, bool nonZero)
    {
        ValidateConcretePoint(vector);
        if (nonZero &&
            RequireCanonicalDecimal(vector, "x_mm") == 0 &&
            RequireCanonicalDecimal(vector, "y_mm") == 0 &&
            RequireCanonicalDecimal(vector, "z_mm") == 0)
        {
            throw Invalid("Concrete displacement must be non-zero.");
        }
    }

    private static decimal RequireCanonicalDecimal(
        JsonElement parent,
        string property,
        bool positive = false)
    {
        var text = RequireBoundedString(parent, property, 40);
        if (!CanonicalDecimalText.IsMatch(text) ||
            !decimal.TryParse(
                text,
                NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint,
                CultureInfo.InvariantCulture,
                out var value) ||
            positive && value <= 0)
        {
            throw Invalid($"{property} is not a canonical bounded decimal.");
        }
        return value;
    }

    private static void ValidateEffectManifest(
        JsonElement effect,
        JsonElement operations,
        JsonElement targetRefs)
    {
        EnsureExact(
            effect,
            [
                "schema_version", "entries", "creates", "modifies", "erases",
                "ensures_non_entity", "risk_floor", "checkpoint_strategy"
            ]);
        RequireString(effect, "schema_version", Phase8Contract.EffectSchemaVersion);
        RequireInteger(effect, "erases", 0);
        var entries = RequireArray(effect, "entries", 1, Phase8Contract.MaxExpandedOperations);
        if (entries.GetArrayLength() != operations.GetArrayLength())
        {
            throw Invalid("Effect entry count does not match operations.");
        }
        var entryItems = entries.EnumerateArray().ToArray();
        var operationItems = operations.EnumerateArray().ToArray();
        var refsById = targetRefs.EnumerateArray().ToDictionary(
            item => item.GetProperty("ref_id").GetString()!,
            item => item,
            StringComparer.Ordinal);
        var createTotal = 0;
        var modifyTotal = 0;
        var ensureTotal = 0;
        for (var index = 0; index < entryItems.Length; index++)
        {
            var entry = entryItems[index];
            var operation = operationItems[index];
            EnsureExact(
                entry,
                [
                    "operation_id", "operation_kind", "operation_version", "effect_class",
                    "entity_type", "creates", "modifies", "erases", "checkpoint_strategy"
                ]);
            RequireIdentifier(entry, "operation_id");
            var kind = RequireBoundedString(entry, "operation_kind", 64);
            if (!Phase8Contract.IsAllowedKind(kind))
            {
                throw Invalid("Effect operation kind is not allowlisted.");
            }
            RequireInteger(entry, "operation_version", 1);
            RequireInteger(entry, "erases", 0);
            if (!StringComparer.Ordinal.Equals(
                    entry.GetProperty("operation_id").GetString(),
                    operation.GetProperty("operation_id").GetString()) ||
                !StringComparer.Ordinal.Equals(
                    kind,
                    operation.GetProperty("kind").GetString()))
            {
                throw Invalid("Effect entries do not match ordered operations.");
            }
            var expectedCreates = kind is "ensure_layer" or "move_entity" ? 0 : 1;
            var expectedModifies = kind == "move_entity" ? 1 : 0;
            RequireInteger(entry, "creates", expectedCreates);
            RequireInteger(entry, "modifies", expectedModifies);
            RequireString(
                entry,
                "effect_class",
                kind == "ensure_layer"
                    ? "ensure_non_entity"
                    : kind == "move_entity"
                        ? "modify_in_place"
                        : "create_only");
            var expectedEntityType = Phase8Contract.TargetKinds.Contains(kind)
                ? refsById[operation.GetProperty("target_ref_id").GetString()!]
                    .GetProperty("entity_type").GetString()!
                : kind switch
                {
                    "ensure_layer" => "LAYER",
                    "create_line" => "LINE",
                    "create_circle" => "CIRCLE",
                    "create_polyline" => "LWPOLYLINE",
                    "create_rectangle" => "RECTANGLE",
                    "create_text" => "TEXT",
                    _ => "DIMENSION_LINEAR"
                };
            RequireString(entry, "entity_type", expectedEntityType);
            RequireString(
                entry,
                "checkpoint_strategy",
                kind == "ensure_layer"
                    ? "none"
                    : kind == "move_entity"
                        ? "cad.rollback.checkpoint/2"
                    : "cad.rollback.checkpoint/1-created-entities");
            createTotal += expectedCreates;
            modifyTotal += expectedModifies;
            ensureTotal += kind == "ensure_layer" ? 1 : 0;
        }
        if (RequireNonNegativeInteger(effect, "creates") != createTotal ||
            RequireNonNegativeInteger(effect, "modifies") != modifyTotal ||
            RequireNonNegativeInteger(effect, "ensures_non_entity") != ensureTotal)
        {
            throw Invalid("Effect totals do not match entries.");
        }
        RequireString(effect, "risk_floor", modifyTotal > 0 ? "medium" : "low");
        RequireString(
            effect,
            "checkpoint_strategy",
            modifyTotal > 0
                ? "cad.rollback.checkpoint/2"
                : "cad.rollback.checkpoint/1-created-entities");
    }

    private static void ValidateExecutionPins(JsonElement binding)
    {
        EnsureExact(
            binding,
            [
                "runtime_id", "runtime_role", "host_family", "host_version", "package_id",
                "package_version", "package_hash", "capability_manifest_hash",
                "operation_registry_version", "operation_registry_hash", "policy_version",
                "rollout_policy_digest"
            ]);
        foreach (var field in new[]
        {
            "runtime_id", "host_family", "host_version", "package_id", "package_version"
        })
        {
            RequireIdentifier(binding, field);
        }
        RequireBoundedString(binding, "operation_registry_version", 256);
        RequireBoundedString(binding, "policy_version", 256);
        RequireString(binding, "runtime_role", "primary");
        RequireDigest(binding, "package_hash");
        RequireDigest(binding, "capability_manifest_hash");
        RequireDigest(binding, "operation_registry_hash");
        RequireDigest(binding, "rollout_policy_digest");
    }

    private static void ValidateTargetRefs(JsonElement refs)
    {
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var reference in refs.EnumerateArray())
        {
            EnsureExact(
                reference,
                [
                    "ref_id", "owner_id", "device_id", "document_id", "snapshot_id",
                    "document_revision", "entity_id", "entity_type", "fingerprint"
                ]);
            var id = RequireIdentifier(reference, "ref_id");
            if (!ids.Add(id))
            {
                throw Invalid("Target ref IDs must be unique.");
            }
            foreach (var field in new[]
            {
                "owner_id", "device_id", "document_id", "snapshot_id", "entity_id",
                "entity_type"
            })
            {
                RequireIdentifier(reference, field);
            }
            RequireBoundedString(reference, "document_revision", 256);
            RequireDigest(reference, "fingerprint");
        }
    }

    private static void ValidateTargetClosure(
        JsonElement plan,
        JsonElement operations,
        JsonElement refs,
        string sourceRegistryVersion)
    {
        var refItems = refs.EnumerateArray().ToArray();
        var refIds = refItems
            .Select(item => item.GetProperty("ref_id").GetString()!)
            .ToArray();
        if (!refIds.SequenceEqual(refIds.OrderBy(value => value, StringComparer.Ordinal)))
        {
            throw Invalid("Materialized target refs must be sorted by ref_id.");
        }
        if (refItems.Select(item => item.GetProperty("owner_id").GetString())
            .Distinct(StringComparer.Ordinal).Count() > 1)
        {
            throw Invalid("Materialized target refs must have one owner.");
        }

        var targetOperations = operations.EnumerateArray()
            .Where(item => Phase8Contract.TargetKinds.Contains(
                item.GetProperty("kind").GetString()!))
            .ToArray();
        var usedRefIds = targetOperations
            .Select(item => item.GetProperty("target_ref_id").GetString()!)
            .ToHashSet(StringComparer.Ordinal);
        if (!usedRefIds.SetEquals(refIds))
        {
            throw Invalid(
                "Materialized target refs must exactly match operation targets.");
        }
        if (usedRefIds.Count > 0 &&
            sourceRegistryVersion != Phase8Contract.Phase8RegistryVersion)
        {
            throw Invalid("Target operations require the Phase 8 core registry.");
        }

        var deviceId = plan.GetProperty("device_id").GetString();
        var documentId = plan.GetProperty("document_id").GetString();
        var snapshotId = plan.GetProperty("source_snapshot_id").GetString();
        var revision = plan.GetProperty("expected_document_revision").GetString();
        foreach (var reference in refItems)
        {
            if (!StringComparer.Ordinal.Equals(
                    reference.GetProperty("device_id").GetString(), deviceId) ||
                !StringComparer.Ordinal.Equals(
                    reference.GetProperty("document_id").GetString(), documentId) ||
                !StringComparer.Ordinal.Equals(
                    reference.GetProperty("snapshot_id").GetString(), snapshotId) ||
                !StringComparer.Ordinal.Equals(
                    reference.GetProperty("document_revision").GetString(), revision))
            {
                throw Invalid("Materialized target ref does not match plan context.");
            }
        }

        var requiredCapabilities = plan.GetProperty("required_capabilities")
            .EnumerateArray()
            .Select(item => item.GetString()!)
            .ToArray();
        if (requiredCapabilities.Distinct(StringComparer.Ordinal).Count() !=
            requiredCapabilities.Length)
        {
            throw Invalid("Required plan capabilities must be unique.");
        }
        var refsById = refItems.ToDictionary(
            item => item.GetProperty("ref_id").GetString()!,
            item => item,
            StringComparer.Ordinal);
        foreach (var operation in targetOperations)
        {
            var kind = operation.GetProperty("kind").GetString()!;
            var refId = operation.GetProperty("target_ref_id").GetString()!;
            var entity = refsById[refId].GetProperty("entity_type")
                .GetString()!.ToLowerInvariant();
            var capability =
                $"cad.op.{kind[..^"_entity".Length]}.{entity}.v1";
            if (!requiredCapabilities.Contains(capability, StringComparer.Ordinal))
            {
                throw Invalid(
                    "Target operation capability is missing from sealed plan.");
            }
        }

        foreach (var group in targetOperations.GroupBy(
                     item => item.GetProperty("target_ref_id").GetString()!,
                     StringComparer.Ordinal))
        {
            var kinds = group.Select(item => item.GetProperty("kind").GetString()!)
                .ToArray();
            if (kinds.Contains("move_entity", StringComparer.Ordinal) &&
                kinds.Length != 1)
            {
                throw Invalid(
                    "An in-place target cannot be reused by another operation.");
            }
        }
    }

    private static void ValidateOpaqueRefs(
        JsonElement parent,
        string property,
        string idProperty,
        string digestProperty,
        string? versionProperty = null)
    {
        var refs = RequireArray(parent, property, 0, 32);
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var reference in refs.EnumerateArray())
        {
            var required = versionProperty is null
                ? new[] { idProperty, "owner_id", "content_type", "byte_length", digestProperty }
                : new[]
                {
                    idProperty, "owner_id", versionProperty, "content_type",
                    "byte_length", digestProperty
                };
            EnsureExact(reference, required);
            var id = RequireIdentifier(reference, idProperty);
            if (!ids.Add(id))
            {
                throw Invalid($"{property} IDs must be unique.");
            }
            RequireIdentifier(reference, "owner_id");
            RequirePositiveInteger(reference, "byte_length");
            var expectedContentType = versionProperty is null
                ? "application/vnd.autocad-mcp.cad-program+json"
                : "application/vnd.autocad-mcp.component+json";
            RequireString(reference, "content_type", expectedContentType);
            RequireDigest(reference, digestProperty);
            if (versionProperty is not null)
            {
                RequireIdentifier(reference, versionProperty);
            }
        }
    }

    private static void ValidateBudgets(JsonElement budgets)
    {
        EnsureExact(
            budgets,
            [
                "max_source_operations", "max_expanded_operations", "max_entities",
                "max_vertices", "max_expression_nodes", "max_coordinate_abs_mm",
                "max_text_bytes"
            ]);
    }

    private static void ValidatePlanBudgets(JsonElement budgets, JsonElement operations)
    {
        EnsureExact(
            budgets,
            [
                "estimated_operations", "hard_max_operations", "estimated_entities",
                "hard_max_entities", "estimated_vertices", "hard_max_vertices",
                "estimated_text_bytes", "hard_max_text_bytes"
            ]);
        var operationItems = operations.EnumerateArray().ToArray();
        var entityCount = operationItems.Count(
            item => item.GetProperty("kind").GetString() != "ensure_layer");
        var vertices = operationItems.Sum(item =>
        {
            var kind = item.GetProperty("kind").GetString();
            return kind == "create_polyline"
                ? item.GetProperty("vertices").GetArrayLength()
                : kind == "create_rectangle" ? 4 : 0;
        });
        var textBytes = operationItems.Sum(item =>
        {
            var kind = item.GetProperty("kind").GetString();
            if (kind == "create_text")
            {
                return Encoding.UTF8.GetByteCount(item.GetProperty("text").GetString()!);
            }
            if (kind == "create_dimension_linear" &&
                item.TryGetProperty("text_override", out var textOverride))
            {
                return Encoding.UTF8.GetByteCount(textOverride.GetString()!);
            }
            return 0;
        });
        var operationCount = operationItems.Length;
        if (RequirePositiveInteger(budgets, "estimated_operations") != operationCount ||
            RequireNonNegativeInteger(budgets, "estimated_entities") != entityCount ||
            RequireNonNegativeInteger(budgets, "estimated_vertices") != vertices ||
            RequireNonNegativeInteger(budgets, "estimated_text_bytes") != textBytes)
        {
            throw Invalid("Estimated plan budgets do not match operations.");
        }
        if (operationCount > RequirePositiveInteger(budgets, "hard_max_operations") ||
            entityCount > RequirePositiveInteger(budgets, "hard_max_entities") ||
            vertices > RequirePositiveInteger(budgets, "hard_max_vertices") ||
            textBytes > RequirePositiveInteger(budgets, "hard_max_text_bytes"))
        {
            throw Invalid("Estimated plan usage exceeds hard budget.");
        }
    }

    private static void ValidateIdentifierArray(
        JsonElement parent,
        string property,
        int maximum,
        bool requireOne = false)
    {
        var array = RequireArray(parent, property, requireOne ? 1 : 0, maximum);
        var values = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in array.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.String ||
                !Identifier.IsMatch(item.GetString() ?? string.Empty) ||
                !values.Add(item.GetString()!))
            {
                throw Invalid($"{property} must contain unique identifiers.");
            }
        }
    }

    private static JsonElement RequireArray(
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

    private static string RequireIdentifier(JsonElement parent, string property)
    {
        var value = RequireBoundedString(parent, property, 128);
        if (!Identifier.IsMatch(value))
        {
            throw Invalid($"{property} is not an identifier.");
        }
        return value;
    }

    private static string RequireDigest(JsonElement parent, string property)
    {
        var value = RequireBoundedString(parent, property, 71);
        if (!Digest.IsMatch(value))
        {
            throw Invalid($"{property} is not a SHA-256 digest.");
        }
        return value;
    }

    private static void RequireExactDigest(
        JsonElement parent,
        string property,
        string expected)
    {
        if (!StringComparer.Ordinal.Equals(RequireDigest(parent, property), expected))
        {
            throw Invalid($"{property} does not match the sealed plan.");
        }
    }

    private static string RequireBoundedString(JsonElement parent, string property, int maximum)
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

    private static void RequireString(JsonElement parent, string property, string expected)
    {
        if (!parent.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.String ||
            !StringComparer.Ordinal.Equals(value.GetString(), expected))
        {
            throw Invalid($"{property} does not match the contract.");
        }
    }

    private static int RequirePositiveInteger(JsonElement parent, string property)
    {
        if (!parent.TryGetProperty(property, out var value) ||
            !value.TryGetInt32(out var parsed) ||
            parsed < 1)
        {
            throw Invalid($"{property} must be a positive integer.");
        }
        return parsed;
    }

    private static int RequireNonNegativeInteger(JsonElement parent, string property)
    {
        if (!parent.TryGetProperty(property, out var value) ||
            !value.TryGetInt32(out var parsed) ||
            parsed < 0)
        {
            throw Invalid($"{property} must be a non-negative integer.");
        }
        return parsed;
    }

    private static void RequireInteger(JsonElement parent, string property, int expected)
    {
        if (!parent.TryGetProperty(property, out var value) ||
            !value.TryGetInt32(out var parsed) ||
            parsed != expected)
        {
            throw Invalid($"{property} does not match the contract.");
        }
    }

    private static void RequireObject(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw Invalid("Contract value must be an object.");
        }
    }

    private static void EnsureExact(
        JsonElement value,
        IReadOnlyCollection<string> required,
        IReadOnlyCollection<string>? optional = null)
    {
        RequireObject(value);
        var allowed = new HashSet<string>(required, StringComparer.Ordinal);
        if (optional is not null)
        {
            allowed.UnionWith(optional);
        }
        var present = value.EnumerateObject().Select(item => item.Name).ToHashSet(
            StringComparer.Ordinal);
        if (present.Count != value.EnumerateObject().Count() ||
            !required.All(present.Contains) ||
            present.Any(item => !allowed.Contains(item)))
        {
            throw Invalid("Contract object has missing or unknown fields.");
        }
    }

    private static string HashWithoutProperty(
        JsonElement value,
        string property,
        string domain)
    {
        var node = JsonNode.Parse(value.GetRawText())!.AsObject();
        node.Remove(property);
        using var document = JsonDocument.Parse(node.ToJsonString());
        return HashDomain(domain, document.RootElement);
    }

    private static string HashWrappedArray(
        string property,
        JsonElement array,
        string domain)
    {
        var node = new JsonObject
        {
            [property] = JsonNode.Parse(array.GetRawText())
        };
        using var document = JsonDocument.Parse(node.ToJsonString());
        return HashDomain(domain, document.RootElement);
    }

    private static string HashWrappedString(string property, string value, string domain)
    {
        var node = new JsonObject { [property] = value };
        using var document = JsonDocument.Parse(node.ToJsonString());
        return HashDomain(domain, document.RootElement);
    }

    private static string HashHardBudgets(JsonElement budgets)
    {
        var node = new JsonObject
        {
            ["max_operations"] = budgets.GetProperty("hard_max_operations").GetInt32(),
            ["max_entities"] = budgets.GetProperty("hard_max_entities").GetInt32(),
            ["max_vertices"] = budgets.GetProperty("hard_max_vertices").GetInt32(),
            ["max_text_bytes"] = budgets.GetProperty("hard_max_text_bytes").GetInt32()
        };
        using var document = JsonDocument.Parse(node.ToJsonString());
        return HashDomain(Phase8Contract.HardBudgetsDigestDomain, document.RootElement);
    }

    private static string Hash(JsonElement value) => $"sha256:{CanonicalJson.Hash(value)}";

    private static string HashDomain(string domain, JsonElement value)
    {
        var node = new JsonObject
        {
            ["domain"] = domain,
            ["payload"] = JsonNode.Parse(value.GetRawText())
        };
        using var document = JsonDocument.Parse(node.ToJsonString());
        return Hash(document.RootElement);
    }

    private static void RequireSame(
        JsonElement left,
        string leftProperty,
        JsonElement right,
        string rightProperty)
    {
        var leftValue = RequireBoundedString(left, leftProperty, 256);
        var rightValue = RequireBoundedString(right, rightProperty, 256);
        if (!StringComparer.Ordinal.Equals(leftValue, rightValue))
        {
            throw Invalid($"{leftProperty} does not match the sealed plan.");
        }
    }

    private static ProtocolValidationException Invalid(string message) =>
        new("program_invalid", message);
}

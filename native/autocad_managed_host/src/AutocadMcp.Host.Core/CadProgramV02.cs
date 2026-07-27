using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutocadMcp.Host.Core;

public static class CadProgramV02Contract
{
    public const string SchemaVersion = "cad.program/0.2";
    public const string RegistryVersion = "cad.program/0.2";
    public const string RegistryDigest =
        "sha256:5dee5cb2d709f06acff2b8678bb084cd9bfa5d1988e9712510c299d61ba30eb8";
    public const string PolicyVersion = "phase6-policy/1";
    public const int MaxOperations = 256;
    public const int MaxEntities = 256;
    public const int MaxLayers = 64;
    public const int MaxVertices = 4096;
    public const int MaxTextBytes = 65_536;
    public const int MaxPayloadBytes = 1_048_576;
    public const int MaxResultBytes = 1_048_576;
    public const int MaxArtifactBytes = 5_242_880;
    public const double MaxCoordinate = 1_000_000_000d;
    public const int MaxDeadlineSeconds = 300;
    public const int MaxPreviewTtlSeconds = 3600;

    public static readonly IReadOnlySet<string> OperationIds = new HashSet<string>(StringComparer.Ordinal)
    {
        "cad.program.preview",
        "cad.program.commit",
        "cad.program.validate"
    };

    public static readonly IReadOnlyList<string> CreateOperationKinds =
    [
        "ensure_layer",
        "create_line",
        "create_circle",
        "create_polyline",
        "create_rectangle",
        "create_text",
        "create_dimension_linear"
    ];
}

public sealed record CadProgramBudgets(
    int MaxOperations,
    int MaxEntities,
    int MaxLayers,
    int MaxVertices,
    int MaxTextBytes,
    int MaxPayloadBytes,
    int MaxResultBytes,
    int MaxArtifactBytes,
    double MaxCoordinateAbs,
    double MaxRadius,
    double MaxTextHeight,
    int ExecutionDeadlineSeconds,
    int PreviewTtlSeconds)
{
    public static CadProgramBudgets Defaults { get; } = new(
        256, 256, 64, 4096, 65_536, 1_048_576, 1_048_576, 5_242_880,
        1_000_000_000d, 1_000_000_000d, 1_000_000_000d, 120, 900);
}

public sealed record CreateRectangleOperation(
    string OperationId,
    string Layer,
    CadPoint FirstCorner,
    CadPoint OppositeCorner) : CadCreateOperation("create_rectangle", OperationId);

public sealed record CreateDimensionLinearOperation(
    string OperationId,
    string Layer,
    CadPoint ExtensionLine1Point,
    CadPoint ExtensionLine2Point,
    CadPoint DimensionLinePoint,
    string? TextOverride) : CadCreateOperation("create_dimension_linear", OperationId);

public abstract record CadProgramPostcondition(string Kind);

public sealed record EntityCountPostcondition(int ExpectedCreated)
    : CadProgramPostcondition("entity_count");

public sealed record LayerExistsPostcondition(string Layer)
    : CadProgramPostcondition("layer_exists");

public sealed record CadProgramV02(
    string ProgramId,
    long ProgramRevision,
    string DeviceId,
    string SourceSnapshotId,
    string DocumentId,
    string ExpectedDocumentRevision,
    IReadOnlyList<CadCreateOperation> Operations,
    IReadOnlyList<CadProgramPostcondition> Postconditions,
    CadProgramBudgets Budgets,
    string ProgramDigest);

public sealed record CadExecutionBinding(
    string ProgramDigest,
    string ExecutionDigest,
    string DocumentId,
    string DocumentRevision,
    string RuntimeId,
    string RuntimeRole,
    string HostFamily,
    string HostVersion,
    string PackageId,
    string PackageVersion,
    string PackageHash,
    string CapabilityManifestHash,
    string OperationRegistryVersion,
    string OperationRegistryHash,
    string PolicyVersion);

public sealed record CadHostBinding(
    string RuntimeId,
    string HostFamily,
    string HostVersion,
    string PackageId,
    string PackageVersion,
    string PackageHash);

public sealed record CadPreviewReference(string PreviewId, string PreviewDigest);

public sealed record CadValidationRequest(
    string ValidationId,
    string ReceiptId,
    int? ExpectedEntityCount,
    IReadOnlyList<string> ExpectedEntityTypes,
    IReadOnlyList<string> ExpectedLayers);

public sealed record CadProgramV02Request(
    CadProgramV02? Program,
    CadExecutionBinding ExecutionBinding,
    string? PreviewId,
    CadPreviewReference? Preview,
    string? ReceiptId,
    CadValidationRequest? Validation);

public static class CadHostAdmission
{
    public static void AssertDeadline(
        DateTimeOffset? deadline,
        DateTimeOffset now,
        int maximumSeconds)
    {
        if (deadline is null ||
            deadline <= now ||
            deadline > now.AddSeconds(maximumSeconds))
        {
            throw new ProtocolValidationException(
                "deadline_expired",
                "CAD Program deadline is expired or exceeds its execution budget.");
        }
    }

    public static void AssertCommandState(int commandActive)
    {
        if ((commandActive & 8) != 0)
        {
            throw new ProtocolValidationException(
                "modal_dialog_active",
                "AutoCAD is waiting for a modal dialog.");
        }
        if (commandActive != 0)
        {
            throw new ProtocolValidationException(
                "autocad_busy",
                "AutoCAD is executing another command.");
        }
    }

    public static void AssertDocument(
        string? commandDocumentId,
        string bindingDocumentId,
        string activeDocumentId,
        string? programDocumentId)
    {
        if (commandDocumentId is null ||
            commandDocumentId != activeDocumentId ||
            bindingDocumentId != activeDocumentId ||
            (programDocumentId is not null && programDocumentId != activeDocumentId))
        {
            throw new ProtocolValidationException(
                "active_document_changed",
                "The active document does not match the exact execution binding.");
        }
    }
}

public sealed record CadBounds(
    double MinX,
    double MinY,
    double MinZ,
    double MaxX,
    double MaxY,
    double MaxZ)
{
    public static CadBounds From(params CadPoint[] points)
    {
        if (points.Length == 0)
        {
            throw new ArgumentException("At least one point is required.", nameof(points));
        }
        return new CadBounds(
            points.Min(point => point.X),
            points.Min(point => point.Y),
            points.Min(point => point.Z),
            points.Max(point => point.X),
            points.Max(point => point.Y),
            points.Max(point => point.Z));
    }
}

public sealed record CadPlannedEntity(
    string OperationId,
    string EntityType,
    string Layer,
    CadBounds Bounds);

public sealed record CadProgramPlan(
    IReadOnlyList<string> Layers,
    IReadOnlyList<CadPlannedEntity> Entities)
{
    public static CadProgramPlan Build(CadProgramV02 program)
    {
        var layers = new List<string>();
        var entities = new List<CadPlannedEntity>();
        foreach (var operation in program.Operations)
        {
            switch (operation)
            {
                case EnsureLayerOperation layer:
                    layers.Add(layer.Name);
                    break;
                case CreateLineOperation line:
                    entities.Add(new(
                        line.OperationId, "LINE", line.Layer, CadBounds.From(line.Start, line.End)));
                    break;
                case CreateCircleOperation circle:
                    entities.Add(new(
                        circle.OperationId,
                        "CIRCLE",
                        circle.Layer,
                        new CadBounds(
                            circle.Center.X - circle.Radius,
                            circle.Center.Y - circle.Radius,
                            circle.Center.Z,
                            circle.Center.X + circle.Radius,
                            circle.Center.Y + circle.Radius,
                            circle.Center.Z)));
                    break;
                case CreatePolylineOperation polyline:
                    entities.Add(new(
                        polyline.OperationId,
                        "POLYLINE",
                        polyline.Layer,
                        CadBounds.From(polyline.Vertices.ToArray())));
                    break;
                case CreateRectangleOperation rectangle:
                    entities.Add(new(
                        rectangle.OperationId,
                        "RECTANGLE",
                        rectangle.Layer,
                        CadBounds.From(rectangle.FirstCorner, rectangle.OppositeCorner)));
                    break;
                case CreateTextOperation text:
                    entities.Add(new(
                        text.OperationId,
                        "TEXT",
                        text.Layer,
                        CadBounds.From(text.Position)));
                    break;
                case CreateDimensionLinearOperation dimension:
                    entities.Add(new(
                        dimension.OperationId,
                        "DIMENSION_LINEAR",
                        dimension.Layer,
                        CadBounds.From(
                            dimension.ExtensionLine1Point,
                            dimension.ExtensionLine2Point,
                            dimension.DimensionLinePoint)));
                    break;
                default:
                    throw new ProtocolValidationException(
                        "capability_missing",
                        "CAD Program operation is outside the create-only registry.");
            }
        }
        return new CadProgramPlan(layers, entities);
    }
}

public static class CadProgramV02Parser
{
    private static readonly HashSet<string> ProgramRequired =
    [
        "program_id", "program_revision", "device_id", "source_snapshot_id",
        "document_id", "expected_document_revision", "operations"
    ];

    private static readonly HashSet<string> ProgramOptional =
    [
        "schema_version", "registry_version", "preconditions", "postconditions", "budgets"
    ];

    public static CadProgramV02Request ParseRequest(string operationId, JsonElement arguments)
    {
        if (!CadProgramV02Contract.OperationIds.Contains(operationId))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "CAD Program operation is not registered.");
        }

        var required = operationId switch
        {
            "cad.program.preview" => new HashSet<string>
            {
                "program", "execution_binding", "preview_id"
            },
            "cad.program.commit" => new HashSet<string>
            {
                "program", "execution_binding", "preview_binding", "receipt_id"
            },
            "cad.program.validate" => new HashSet<string>
            {
                "execution_binding", "validation"
            },
            _ => throw new InvalidOperationException()
        };
        EnsureObject(arguments, required, [], "CAD Program request");

        var binding = ParseExecutionBinding(arguments.GetProperty("execution_binding"));
        CadProgramV02? program = null;
        string? previewId = null;
        CadPreviewReference? preview = null;
        string? receiptId = null;
        CadValidationRequest? validation = null;
        if (operationId is "cad.program.preview" or "cad.program.commit")
        {
            program = ParseProgram(arguments.GetProperty("program"));
            if (!FixedEquals(program.ProgramDigest, binding.ProgramDigest) ||
                program.DocumentId != binding.DocumentId ||
                program.ExpectedDocumentRevision != binding.DocumentRevision)
            {
                throw Invalid("Execution binding does not match the CAD Program.");
            }
        }
        if (operationId == "cad.program.commit")
        {
            preview = ParsePreview(arguments.GetProperty("preview_binding"));
            receiptId = Identifier(arguments, "receipt_id", 128);
        }
        if (operationId == "cad.program.preview")
        {
            previewId = Identifier(arguments, "preview_id", 128);
        }
        if (operationId == "cad.program.validate")
        {
            validation = ParseValidation(arguments.GetProperty("validation"));
        }
        return new CadProgramV02Request(
            program,
            binding,
            previewId,
            preview,
            receiptId,
            validation);
    }

    public static CadProgramV02 ParseProgram(JsonElement value)
    {
        EnsureObject(value, ProgramRequired, ProgramOptional, "CAD Program");
        var schemaVersion = OptionalString(value, "schema_version") ?? CadProgramV02Contract.SchemaVersion;
        var registryVersion = OptionalString(value, "registry_version") ?? CadProgramV02Contract.RegistryVersion;
        if (schemaVersion != CadProgramV02Contract.SchemaVersion ||
            registryVersion != CadProgramV02Contract.RegistryVersion)
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "Unsupported CAD Program schema or registry version.");
        }

        var budgets = value.TryGetProperty("budgets", out var budgetValue)
            ? ParseBudgets(budgetValue)
            : CadProgramBudgets.Defaults;
        var operationValues = value.GetProperty("operations");
        if (operationValues.ValueKind != JsonValueKind.Array ||
            operationValues.GetArrayLength() < 1 ||
            operationValues.GetArrayLength() > budgets.MaxOperations ||
            operationValues.GetArrayLength() > CadProgramV02Contract.MaxOperations)
        {
            throw Invalid("CAD Program operation count is outside the budget.");
        }

        var operations = new List<CadCreateOperation>();
        var operationIds = new HashSet<string>(StringComparer.Ordinal);
        var layerOutputs = new Dictionary<string, string>(StringComparer.Ordinal);
        var entityCount = 0;
        var layerCount = 0;
        var vertices = 0;
        var textBytes = 0;
        foreach (var operationValue in operationValues.EnumerateArray())
        {
            var operation = ParseOperation(operationValue, layerOutputs);
            if (!operationIds.Add(operation.OperationId))
            {
                throw Invalid("CAD Program operation_id values must be unique.");
            }
            if (operation is EnsureLayerOperation layer)
            {
                layerCount++;
                layerOutputs[operation.OperationId] = layer.Name;
            }
            else
            {
                entityCount++;
            }
            if (operation is CreatePolylineOperation polyline)
            {
                vertices += polyline.Vertices.Count;
            }
            else if (operation is CreateRectangleOperation)
            {
                vertices += 4;
            }
            if (operation is CreateTextOperation text)
            {
                textBytes += Encoding.UTF8.GetByteCount(text.Text);
            }
            operations.Add(operation);
        }
        if (entityCount > budgets.MaxEntities ||
            layerCount > budgets.MaxLayers ||
            vertices > budgets.MaxVertices ||
            textBytes > budgets.MaxTextBytes)
        {
            throw Invalid("CAD Program exceeds an entity, layer, vertex, or text budget.");
        }
        AssertOperationBounds(operations, budgets);

        var documentId = Identifier(value, "document_id", 128);
        var expectedRevision = Revision(value, "expected_document_revision");
        ParsePreconditions(value, documentId, expectedRevision);
        var postconditions = ParsePostconditions(value, layerOutputs);
        var normalized = NormalizeProgram(value);
        var canonical = CanonicalJson.Serialize(normalized);
        if (Encoding.UTF8.GetByteCount(canonical) > budgets.MaxPayloadBytes)
        {
            throw Invalid("CAD Program canonical payload exceeds max_payload_bytes.");
        }
        var digest = $"sha256:{Convert.ToHexString(
            SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant()}";
        return new CadProgramV02(
            Identifier(value, "program_id", 128),
            PositiveInteger(value, "program_revision"),
            Identifier(value, "device_id", 128),
            Identifier(value, "source_snapshot_id", 128),
            documentId,
            expectedRevision,
            operations,
            postconditions,
            budgets,
            digest);
    }

    public static void AssertHostBinding(CadExecutionBinding binding, CadHostBinding host)
    {
        if (binding.RuntimeId != host.RuntimeId ||
            binding.RuntimeRole != "primary" ||
            binding.HostFamily != "R25" ||
            binding.HostFamily != host.HostFamily ||
            binding.HostVersion != host.HostVersion ||
            binding.PackageId != host.PackageId ||
            binding.PackageVersion != host.PackageVersion ||
            !FixedEquals(binding.PackageHash, host.PackageHash))
        {
            throw new ProtocolValidationException(
                "runtime_changed",
                "Runtime or package binding does not match this Managed Host.");
        }
        if (binding.OperationRegistryVersion != CadProgramV02Contract.RegistryVersion ||
            !FixedEquals(binding.OperationRegistryHash, CadProgramV02Contract.RegistryDigest))
        {
            throw new ProtocolValidationException(
                "capability_missing",
                "CAD Program operation registry binding is unsupported.");
        }
        if (binding.PolicyVersion != CadProgramV02Contract.PolicyVersion)
        {
            throw new ProtocolValidationException(
                "runtime_changed",
                "CAD Program policy binding is unsupported.");
        }
    }

    public static string BuildPreviewDigest(
        string previewId,
        CadProgramV02 program,
        CadExecutionBinding binding)
    {
        var value = new JsonObject
        {
            ["preview_id"] = previewId,
            ["program_digest"] = program.ProgramDigest,
            ["document_id"] = binding.DocumentId,
            ["document_revision"] = binding.DocumentRevision,
            ["runtime_id"] = binding.RuntimeId,
            ["runtime_role"] = binding.RuntimeRole,
            ["host_family"] = binding.HostFamily,
            ["host_version"] = binding.HostVersion,
            ["package_id"] = binding.PackageId,
            ["package_version"] = binding.PackageVersion,
            ["package_hash"] = binding.PackageHash,
            ["capability_manifest_hash"] = binding.CapabilityManifestHash,
            ["operation_registry_version"] = binding.OperationRegistryVersion,
            ["operation_registry_hash"] = binding.OperationRegistryHash,
            ["policy_version"] = binding.PolicyVersion
        };
        using var document = JsonDocument.Parse(value.ToJsonString());
        return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
    }

    private static CadCreateOperation ParseOperation(
        JsonElement value,
        IReadOnlyDictionary<string, string> layerOutputs)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw Invalid("CAD Program operation must be an object.");
        }
        var kind = RequiredString(value, "kind", 64);
        return kind switch
        {
            "ensure_layer" => ParseLayer(value),
            "create_line" => ParseLine(value, layerOutputs),
            "create_circle" => ParseCircle(value, layerOutputs),
            "create_polyline" => ParsePolyline(value, layerOutputs),
            "create_rectangle" => ParseRectangle(value, layerOutputs),
            "create_text" => ParseText(value, layerOutputs),
            "create_dimension_linear" => ParseDimension(value, layerOutputs),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "CAD Program operation is outside the exact create-only registry.")
        };
    }

    private static void AssertOperationBounds(
        IEnumerable<CadCreateOperation> operations,
        CadProgramBudgets budgets)
    {
        foreach (var operation in operations)
        {
            IEnumerable<CadPoint> points = operation switch
            {
                CreateLineOperation line => [line.Start, line.End],
                CreateCircleOperation circle => [circle.Center],
                CreatePolylineOperation polyline => polyline.Vertices,
                CreateRectangleOperation rectangle => [rectangle.FirstCorner, rectangle.OppositeCorner],
                CreateTextOperation text => [text.Position],
                CreateDimensionLinearOperation dimension =>
                [
                    dimension.ExtensionLine1Point,
                    dimension.ExtensionLine2Point,
                    dimension.DimensionLinePoint
                ],
                _ => []
            };
            if (points.Any(point =>
                    Math.Abs(point.X) > budgets.MaxCoordinateAbs ||
                    Math.Abs(point.Y) > budgets.MaxCoordinateAbs ||
                    Math.Abs(point.Z) > budgets.MaxCoordinateAbs))
            {
                throw Invalid("CAD Program coordinate exceeds max_coordinate_abs.");
            }
            if (operation is CreateCircleOperation circleOperation &&
                circleOperation.Radius > budgets.MaxRadius)
            {
                throw Invalid("CAD Program circle radius exceeds max_radius.");
            }
            if (operation is CreateTextOperation textOperation &&
                textOperation.Height > budgets.MaxTextHeight)
            {
                throw Invalid("CAD Program text height exceeds max_text_height.");
            }
        }
    }

    private static EnsureLayerOperation ParseLayer(JsonElement value)
    {
        EnsureObject(
            value,
            ["kind", "operation_id", "name"],
            ["color_index"],
            "ensure_layer");
        short? color = null;
        if (value.TryGetProperty("color_index", out var colorValue) &&
            colorValue.ValueKind != JsonValueKind.Null)
        {
            if (!colorValue.TryGetInt16(out var parsed) || parsed is < 1 or > 255)
            {
                throw Invalid("Layer color_index must be between 1 and 255.");
            }
            color = parsed;
        }
        return new EnsureLayerOperation(
            Identifier(value, "operation_id", 128),
            LayerName(value, "name"),
            color);
    }

    private static CreateLineOperation ParseLine(
        JsonElement value,
        IReadOnlyDictionary<string, string> layers)
    {
        EnsureObject(value, ["kind", "operation_id", "layer", "start", "end"], [], "create_line");
        var start = Point(value.GetProperty("start"));
        var end = Point(value.GetProperty("end"));
        if (start == end)
        {
            throw Invalid("Line start and end must differ.");
        }
        return new(
            Identifier(value, "operation_id", 128),
            LayerTarget(value.GetProperty("layer"), layers),
            start,
            end);
    }

    private static CreateCircleOperation ParseCircle(
        JsonElement value,
        IReadOnlyDictionary<string, string> layers)
    {
        EnsureObject(
            value,
            ["kind", "operation_id", "layer", "center", "radius"],
            [],
            "create_circle");
        return new(
            Identifier(value, "operation_id", 128),
            LayerTarget(value.GetProperty("layer"), layers),
            Point(value.GetProperty("center")),
            FiniteNumber(value, "radius", 0, CadProgramV02Contract.MaxCoordinate, true));
    }

    private static CreatePolylineOperation ParsePolyline(
        JsonElement value,
        IReadOnlyDictionary<string, string> layers)
    {
        EnsureObject(
            value,
            ["kind", "operation_id", "layer", "vertices"],
            ["closed"],
            "create_polyline");
        var vertices = value.GetProperty("vertices");
        if (vertices.ValueKind != JsonValueKind.Array ||
            vertices.GetArrayLength() is < 2 or > CadProgramV02Contract.MaxVertices)
        {
            throw Invalid("Polyline must contain between 2 and 4096 vertices.");
        }
        var points = vertices.EnumerateArray().Select(Point).ToArray();
        if (points.Distinct().Count() < 2)
        {
            throw Invalid("Polyline must contain at least two distinct vertices.");
        }
        var closed = value.TryGetProperty("closed", out var closedValue)
            ? Boolean(closedValue, "closed")
            : false;
        return new(
            Identifier(value, "operation_id", 128),
            LayerTarget(value.GetProperty("layer"), layers),
            points,
            closed);
    }

    private static CreateRectangleOperation ParseRectangle(
        JsonElement value,
        IReadOnlyDictionary<string, string> layers)
    {
        EnsureObject(
            value,
            ["kind", "operation_id", "layer", "first_corner", "opposite_corner"],
            [],
            "create_rectangle");
        var first = Point(value.GetProperty("first_corner"));
        var opposite = Point(value.GetProperty("opposite_corner"));
        if (first.X == opposite.X || first.Y == opposite.Y || first.Z != opposite.Z)
        {
            throw Invalid("Rectangle corners must define a non-zero planar rectangle.");
        }
        return new(
            Identifier(value, "operation_id", 128),
            LayerTarget(value.GetProperty("layer"), layers),
            first,
            opposite);
    }

    private static CreateTextOperation ParseText(
        JsonElement value,
        IReadOnlyDictionary<string, string> layers)
    {
        EnsureObject(
            value,
            ["kind", "operation_id", "layer", "position", "text", "height"],
            ["rotation_radians"],
            "create_text");
        var text = RequiredString(value, "text", 65_536);
        if (text.Any(character =>
                char.IsControl(character) && character is not '\r' and not '\n' and not '\t'))
        {
            throw Invalid("Text contains unsupported control characters.");
        }
        var rotation = value.TryGetProperty("rotation_radians", out _)
            ? FiniteNumber(value, "rotation_radians", -Math.PI * 2, Math.PI * 2)
            : 0;
        return new(
            Identifier(value, "operation_id", 128),
            LayerTarget(value.GetProperty("layer"), layers),
            Point(value.GetProperty("position")),
            text,
            FiniteNumber(value, "height", 0, CadProgramV02Contract.MaxCoordinate, true),
            rotation);
    }

    private static CreateDimensionLinearOperation ParseDimension(
        JsonElement value,
        IReadOnlyDictionary<string, string> layers)
    {
        EnsureObject(
            value,
            [
                "kind", "operation_id", "layer", "extension_line1_point",
                "extension_line2_point", "dimension_line_point"
            ],
            ["text_override"],
            "create_dimension_linear");
        var first = Point(value.GetProperty("extension_line1_point"));
        var second = Point(value.GetProperty("extension_line2_point"));
        if (first == second)
        {
            throw Invalid("Linear dimension extension points must differ.");
        }
        string? textOverride = null;
        if (value.TryGetProperty("text_override", out var textValue) &&
            textValue.ValueKind != JsonValueKind.Null)
        {
            if (textValue.ValueKind != JsonValueKind.String ||
                textValue.GetString() is not { Length: <= 1024 } parsed)
            {
                throw Invalid("Dimension text_override is invalid.");
            }
            textOverride = parsed;
        }
        return new(
            Identifier(value, "operation_id", 128),
            LayerTarget(value.GetProperty("layer"), layers),
            first,
            second,
            Point(value.GetProperty("dimension_line_point")),
            textOverride);
    }

    private static CadProgramBudgets ParseBudgets(JsonElement value)
    {
        var fields = new HashSet<string>
        {
            "max_operations", "max_entities", "max_layers", "max_vertices",
            "max_text_bytes", "max_payload_bytes", "max_result_bytes",
            "max_artifact_bytes", "max_coordinate_abs", "max_radius",
            "max_text_height", "execution_deadline_seconds", "preview_ttl_seconds"
        };
        EnsureObject(value, [], fields, "program budgets");
        var defaults = CadProgramBudgets.Defaults;
        return new CadProgramBudgets(
            BoundedInteger(value, "max_operations", defaults.MaxOperations, 1, 256),
            BoundedInteger(value, "max_entities", defaults.MaxEntities, 1, 256),
            BoundedInteger(value, "max_layers", defaults.MaxLayers, 1, 64),
            BoundedInteger(value, "max_vertices", defaults.MaxVertices, 2, 4096),
            BoundedInteger(value, "max_text_bytes", defaults.MaxTextBytes, 1, 65_536),
            BoundedInteger(value, "max_payload_bytes", defaults.MaxPayloadBytes, 1024, 1_048_576),
            BoundedInteger(value, "max_result_bytes", defaults.MaxResultBytes, 1024, 1_048_576),
            BoundedInteger(value, "max_artifact_bytes", defaults.MaxArtifactBytes, 1024, 5_242_880),
            BoundedNumber(value, "max_coordinate_abs", defaults.MaxCoordinateAbs),
            BoundedNumber(value, "max_radius", defaults.MaxRadius),
            BoundedNumber(value, "max_text_height", defaults.MaxTextHeight),
            BoundedInteger(value, "execution_deadline_seconds", defaults.ExecutionDeadlineSeconds, 1, 300),
            BoundedInteger(value, "preview_ttl_seconds", defaults.PreviewTtlSeconds, 1, 3600));
    }

    private static CadExecutionBinding ParseExecutionBinding(JsonElement value)
    {
        var fields = new HashSet<string>
        {
            "program_digest", "execution_digest", "document_id", "document_revision",
            "runtime_id", "runtime_role", "host_family", "host_version", "package_id",
            "package_version", "package_hash", "capability_manifest_hash",
            "operation_registry_version", "operation_registry_hash", "policy_version"
        };
        EnsureObject(value, fields, [], "execution binding");
        return new CadExecutionBinding(
            Digest(value, "program_digest"),
            Digest(value, "execution_digest"),
            Identifier(value, "document_id", 128),
            Revision(value, "document_revision"),
            Identifier(value, "runtime_id", 64),
            RequiredString(value, "runtime_role", 32),
            RequiredString(value, "host_family", 32),
            RequiredString(value, "host_version", 64),
            Identifier(value, "package_id", 128),
            RequiredString(value, "package_version", 64),
            Digest(value, "package_hash"),
            Digest(value, "capability_manifest_hash"),
            RequiredString(value, "operation_registry_version", 64),
            Digest(value, "operation_registry_hash"),
            RequiredString(value, "policy_version", 64));
    }

    private static CadPreviewReference ParsePreview(JsonElement value)
    {
        EnsureObject(value, ["preview_id", "preview_digest"], [], "preview binding");
        return new(
            Identifier(value, "preview_id", 128),
            Digest(value, "preview_digest"));
    }

    private static CadValidationRequest ParseValidation(JsonElement value)
    {
        EnsureObject(
            value,
            ["validation_id", "receipt_id"],
            ["expected_entity_count", "expected_entity_types", "expected_layers"],
            "validation request");
        int? expectedCount = null;
        if (value.TryGetProperty("expected_entity_count", out var count))
        {
            if (!count.TryGetInt32(out var parsed) || parsed is < 0 or > 256)
            {
                throw Invalid("expected_entity_count is outside the allowed range.");
            }
            expectedCount = parsed;
        }
        return new CadValidationRequest(
            Identifier(value, "validation_id", 128),
            Identifier(value, "receipt_id", 128),
            expectedCount,
            StringArray(value, "expected_entity_types", 16, 64, UpperType),
            StringArray(value, "expected_layers", 64, 255, LayerName));
    }

    private static void ParsePreconditions(
        JsonElement program,
        string documentId,
        string expectedRevision)
    {
        if (!program.TryGetProperty("preconditions", out var values))
        {
            return;
        }
        if (values.ValueKind != JsonValueKind.Array || values.GetArrayLength() > 16)
        {
            throw Invalid("CAD Program preconditions are invalid.");
        }
        foreach (var value in values.EnumerateArray())
        {
            EnsureObject(
                value,
                ["kind", "document_id", "expected_document_revision"],
                [],
                "document revision precondition");
            if (RequiredString(value, "kind", 64) != "document_revision_equals" ||
                Identifier(value, "document_id", 128) != documentId ||
                Revision(value, "expected_document_revision") != expectedRevision)
            {
                throw Invalid("Document revision precondition does not match the program.");
            }
        }
    }

    private static IReadOnlyList<CadProgramPostcondition> ParsePostconditions(
        JsonElement program,
        IReadOnlyDictionary<string, string> layers)
    {
        if (!program.TryGetProperty("postconditions", out var values))
        {
            return [];
        }
        if (values.ValueKind != JsonValueKind.Array || values.GetArrayLength() > 64)
        {
            throw Invalid("CAD Program postconditions are invalid.");
        }
        var result = new List<CadProgramPostcondition>();
        foreach (var value in values.EnumerateArray())
        {
            var kind = RequiredString(value, "kind", 64);
            switch (kind)
            {
                case "entity_count":
                    EnsureObject(value, ["kind", "expected_created"], [], "entity count postcondition");
                    if (!value.GetProperty("expected_created").TryGetInt32(out var expected) ||
                        expected is < 0 or > 256)
                    {
                        throw Invalid("Entity count postcondition is invalid.");
                    }
                    result.Add(new EntityCountPostcondition(expected));
                    break;
                case "layer_exists":
                    EnsureObject(value, ["kind", "layer"], [], "layer exists postcondition");
                    result.Add(new LayerExistsPostcondition(
                        LayerTarget(value.GetProperty("layer"), layers)));
                    break;
                default:
                    throw Invalid("CAD Program postcondition kind is unsupported.");
            }
        }
        return result;
    }

    private static JsonElement NormalizeProgram(JsonElement source)
    {
        var root = JsonNode.Parse(source.GetRawText())?.AsObject()
            ?? throw Invalid("CAD Program must be an object.");
        root["schema_version"] ??= CadProgramV02Contract.SchemaVersion;
        root["registry_version"] ??= CadProgramV02Contract.RegistryVersion;
        root["preconditions"] ??= new JsonArray();
        root["postconditions"] ??= new JsonArray();
        root["budgets"] ??= JsonNode.Parse(
            """
            {"max_operations":256,"max_entities":256,"max_layers":64,"max_vertices":4096,"max_text_bytes":65536,"max_payload_bytes":1048576,"max_result_bytes":1048576,"max_artifact_bytes":5242880,"max_coordinate_abs":1000000000.0,"max_radius":1000000000.0,"max_text_height":1000000000.0,"execution_deadline_seconds":120,"preview_ttl_seconds":900}
            """);
        var budgets = root["budgets"]!.AsObject();
        AddBudgetDefaults(budgets);
        foreach (var operationNode in root["operations"]!.AsArray())
        {
            var operation = operationNode!.AsObject();
            var kind = operation["kind"]!.GetValue<string>();
            if (kind == "create_polyline")
            {
                operation["closed"] ??= false;
            }
            else if (kind == "create_text")
            {
                operation["rotation_radians"] ??= JsonNode.Parse("0.0");
            }
            NormalizeLayerRef(operation["layer"]);
        }
        foreach (var postconditionNode in root["postconditions"]!.AsArray())
        {
            NormalizeLayerRef(postconditionNode?["layer"]);
        }
        using var document = JsonDocument.Parse(root.ToJsonString());
        return document.RootElement.Clone();
    }

    private static void AddBudgetDefaults(JsonObject budgets)
    {
        var defaults = JsonNode.Parse(
            """
            {"max_operations":256,"max_entities":256,"max_layers":64,"max_vertices":4096,"max_text_bytes":65536,"max_payload_bytes":1048576,"max_result_bytes":1048576,"max_artifact_bytes":5242880,"max_coordinate_abs":1000000000.0,"max_radius":1000000000.0,"max_text_height":1000000000.0,"execution_deadline_seconds":120,"preview_ttl_seconds":900}
            """)!.AsObject();
        foreach (var item in defaults)
        {
            if (!budgets.ContainsKey(item.Key))
            {
                budgets[item.Key] = item.Value?.DeepClone();
            }
        }
    }

    private static void NormalizeLayerRef(JsonNode? node)
    {
        if (node is JsonObject reference)
        {
            reference["output"] ??= "layer";
        }
    }

    private static string LayerTarget(
        JsonElement value,
        IReadOnlyDictionary<string, string> layerOutputs)
    {
        if (value.ValueKind == JsonValueKind.String)
        {
            return LayerName(value.GetString() ?? string.Empty);
        }
        EnsureObject(value, ["operation_id"], ["output"], "layer output reference");
        var operationId = Identifier(value, "operation_id", 128);
        var output = value.TryGetProperty("output", out var outputValue)
            ? ElementString(outputValue, "output", 16)
            : "layer";
        if (output != "layer" || !layerOutputs.TryGetValue(operationId, out var layer))
        {
            throw Invalid("Layer reference must target an earlier ensure_layer output.");
        }
        return layer;
    }

    private static CadPoint Point(JsonElement value)
    {
        EnsureObject(value, ["x", "y", "z"], [], "point");
        return new CadPoint(
            FiniteNumber(value, "x", -CadProgramV02Contract.MaxCoordinate, CadProgramV02Contract.MaxCoordinate),
            FiniteNumber(value, "y", -CadProgramV02Contract.MaxCoordinate, CadProgramV02Contract.MaxCoordinate),
            FiniteNumber(value, "z", -CadProgramV02Contract.MaxCoordinate, CadProgramV02Contract.MaxCoordinate));
    }

    private static IReadOnlyList<string> StringArray(
        JsonElement parent,
        string name,
        int maximumItems,
        int maximumLength,
        Func<string, string> validator)
    {
        if (!parent.TryGetProperty(name, out var value))
        {
            return [];
        }
        if (value.ValueKind != JsonValueKind.Array || value.GetArrayLength() > maximumItems)
        {
            throw Invalid($"{name} is invalid.");
        }
        var result = value.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String
                ? validator(item.GetString() ?? string.Empty)
                : throw Invalid($"{name} contains a non-string value."))
            .ToArray();
        if (result.Any(item => item.Length > maximumLength) ||
            result.Distinct(StringComparer.Ordinal).Count() != result.Length)
        {
            throw Invalid($"{name} is invalid or contains duplicates.");
        }
        return result;
    }

    private static string UpperType(string value)
    {
        if (value.Length is < 1 or > 64 ||
            value.Any(character => !(char.IsAsciiLetterUpper(character) ||
                                      char.IsAsciiDigit(character) ||
                                      character == '_')))
        {
            throw Invalid("Entity type is invalid.");
        }
        return value;
    }

    private static string LayerName(JsonElement value, string name) =>
        LayerName(RequiredString(value, name, 255));

    private static string LayerName(string value)
    {
        if (value.Length is < 1 or > 255 ||
            value.Any(char.IsControl) ||
            value.IndexOfAny(['<', '>', '/', '\\', '"', ':', ';', '?', '*', '|', '=', '`']) >= 0)
        {
            throw Invalid("Layer name is invalid.");
        }
        return value;
    }

    private static string Identifier(JsonElement value, string name, int maximum)
    {
        var identifier = RequiredString(value, name, maximum);
        if (!char.IsAsciiLetterOrDigit(identifier[0]) ||
            identifier.Any(character =>
                !(char.IsAsciiLetterOrDigit(character) || character is '.' or '_' or '-')))
        {
            throw Invalid($"{name} contains invalid characters.");
        }
        return identifier;
    }

    private static string Revision(JsonElement value, string name)
    {
        var revision = RequiredString(value, name, 256);
        if (revision.Any(char.IsWhiteSpace))
        {
            throw Invalid($"{name} contains whitespace.");
        }
        return revision;
    }

    private static string Digest(JsonElement value, string name)
    {
        var digest = RequiredString(value, name, 71);
        if (digest.Length != 71 ||
            !digest.StartsWith("sha256:", StringComparison.Ordinal) ||
            digest[7..].Any(character => !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw Invalid($"{name} must be a lowercase SHA-256 digest.");
        }
        return digest;
    }

    private static double FiniteNumber(
        JsonElement value,
        string name,
        double minimum,
        double maximum,
        bool exclusiveMinimum = false)
    {
        if (!value.TryGetProperty(name, out var property) ||
            property.ValueKind != JsonValueKind.Number ||
            !property.TryGetDouble(out var number) ||
            !double.IsFinite(number) ||
            number > maximum ||
            (exclusiveMinimum ? number <= minimum : number < minimum))
        {
            throw Invalid($"{name} is outside the allowed range.");
        }
        return number;
    }

    private static int BoundedInteger(
        JsonElement parent,
        string name,
        int defaultValue,
        int minimum,
        int maximum)
    {
        if (!parent.TryGetProperty(name, out var value))
        {
            return defaultValue;
        }
        if (!value.TryGetInt32(out var parsed) || parsed < minimum || parsed > maximum)
        {
            throw Invalid($"{name} is outside the allowed range.");
        }
        return parsed;
    }

    private static double BoundedNumber(
        JsonElement parent,
        string name,
        double defaultValue)
    {
        if (!parent.TryGetProperty(name, out _))
        {
            return defaultValue;
        }
        return FiniteNumber(parent, name, 0, CadProgramV02Contract.MaxCoordinate, true);
    }

    private static long PositiveInteger(JsonElement value, string name)
    {
        if (!value.TryGetProperty(name, out var property) ||
            !property.TryGetInt64(out var number) ||
            number < 1)
        {
            throw Invalid($"{name} must be a positive integer.");
        }
        return number;
    }

    private static bool Boolean(JsonElement value, string name)
    {
        if (value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
        {
            throw Invalid($"{name} must be boolean.");
        }
        return value.GetBoolean();
    }

    private static string RequiredString(JsonElement value, string name, int maximum)
    {
        if (!value.TryGetProperty(name, out var property))
        {
            throw Invalid($"{name} is required.");
        }
        return ElementString(property, name, maximum);
    }

    private static string ElementString(JsonElement value, string name, int maximum)
    {
        if (value.ValueKind != JsonValueKind.String ||
            value.GetString() is not { Length: > 0 } text ||
            text.Length > maximum)
        {
            throw Invalid($"{name} is invalid.");
        }
        return text;
    }

    private static string? OptionalString(JsonElement value, string name) =>
        value.TryGetProperty(name, out var property)
            ? ElementString(property, name, 64)
            : null;

    private static void EnsureObject(
        JsonElement value,
        IEnumerable<string> required,
        IEnumerable<string> optional,
        string subject)
    {
        var requiredSet = required.ToHashSet(StringComparer.Ordinal);
        var allowed = requiredSet.Concat(optional).ToHashSet(StringComparer.Ordinal);
        if (value.ValueKind != JsonValueKind.Object ||
            value.EnumerateObject().Any(property => !allowed.Contains(property.Name)) ||
            requiredSet.Any(field => !value.TryGetProperty(field, out _)))
        {
            throw Invalid($"{subject} fields are missing or unknown.");
        }
    }

    private static bool FixedEquals(string left, string right) =>
        left.Length == right.Length &&
        CryptographicOperations.FixedTimeEquals(
            Encoding.ASCII.GetBytes(left),
            Encoding.ASCII.GetBytes(right));

    private static ProtocolValidationException Invalid(string message) =>
        new("program_invalid", message);
}

public sealed record CadPreviewRecord(
    string PreviewId,
    string PreviewDigest,
    string ProgramDigest,
    string BindingDigest,
    DateTimeOffset ExpiresAt);

public sealed class CadPreviewLedger(int capacity = 256)
{
    private readonly Dictionary<string, CadPreviewRecord> _records = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public void Add(CadPreviewRecord record)
    {
        lock (_gate)
        {
            if (_records.Count >= capacity)
            {
                var oldest = _records.Values.MinBy(item => item.ExpiresAt);
                if (oldest is not null)
                {
                    _records.Remove(oldest.PreviewId);
                }
            }
            _records[record.PreviewId] = record;
        }
    }

    public CadPreviewRecord Require(
        CadPreviewReference reference,
        string programDigest,
        string bindingDigest,
        DateTimeOffset now)
    {
        lock (_gate)
        {
            if (!_records.TryGetValue(reference.PreviewId, out var record) ||
                record.ExpiresAt <= now)
            {
                _records.Remove(reference.PreviewId);
                throw new ProtocolValidationException(
                    "preview_required",
                    "Preview is missing or expired.");
            }
            if (!Fixed(record.PreviewDigest, reference.PreviewDigest) ||
                !Fixed(record.ProgramDigest, programDigest) ||
                !Fixed(record.BindingDigest, bindingDigest))
            {
                throw new ProtocolValidationException(
                    "runtime_changed",
                    "Preview binding changed before commit.");
            }
            return record;
        }
    }

    private static bool Fixed(string left, string right) =>
        left.Length == right.Length &&
        CryptographicOperations.FixedTimeEquals(
            Encoding.ASCII.GetBytes(left),
            Encoding.ASCII.GetBytes(right));
}

public sealed record DurableEntityEvidence(
    string Handle,
    string EntityType,
    string Layer,
    CadBounds Bounds);

public sealed record DurableProgramReceiptV02(
    string IdempotencyKey,
    string ProgramDigest,
    string ExecutionDigest,
    string DocumentId,
    string DocumentRevisionBefore,
    string DocumentRevisionAfter,
    IReadOnlyList<DurableEntityEvidence> Entities,
    IReadOnlyList<string> Layers)
{
    public const string RecordVersion = "cad.program.receipt/2";

    public string ReceiptId
    {
        get => BuildReceiptId(IdempotencyKey);
    }

    public static string BuildReceiptId(string previewId)
    {
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(previewId));
        return $"AUTOCAD_MCP_PROGRAM_{Convert.ToHexString(digest).ToLowerInvariant()[..32]}";
    }

    public string ReceiptDigest
    {
        get
        {
            using var document = JsonDocument.Parse(Serialize());
            return $"sha256:{CanonicalJson.Hash(document.RootElement)}";
        }
    }

    public string Serialize()
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(
            new
            {
                record_version = RecordVersion,
                idempotency_key = IdempotencyKey,
                program_digest = ProgramDigest,
                execution_digest = ExecutionDigest,
                document_id = DocumentId,
                document_revision_before = DocumentRevisionBefore,
                document_revision_after = DocumentRevisionAfter,
                entities = Entities,
                layers = Layers
            },
            HostProtocol.JsonOptions);
        if (bytes.Length > 65_536)
        {
            throw new ProtocolValidationException(
                "program_invalid",
                "Durable receipt exceeds the bounded DWG record size.");
        }
        return Encoding.UTF8.GetString(bytes);
    }

    public static DurableProgramReceiptV02 Parse(string json)
    {
        if (json.Length is < 1 or > 65_536)
        {
            throw Invalid();
        }
        try
        {
            using var document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 16
            });
            var root = document.RootElement;
            EnsureExact(
                root,
                [
                    "record_version", "idempotency_key", "program_digest", "execution_digest",
                    "document_id", "document_revision_before", "document_revision_after",
                    "entities", "layers"
                ]);
            if (root.GetProperty("record_version").GetString() != RecordVersion)
            {
                throw Invalid();
            }
            var entities = root.GetProperty("entities").EnumerateArray()
                .Select(ParseEntity)
                .ToArray();
            if (entities.Length > 256)
            {
                throw Invalid();
            }
            var layers = root.GetProperty("layers").EnumerateArray()
                .Select(item => ReceiptText(item, 255))
                .ToArray();
            if (layers.Length > 64)
            {
                throw Invalid();
            }
            var receipt = new DurableProgramReceiptV02(
                ReceiptText(root.GetProperty("idempotency_key"), 128),
                ParseDigest(root.GetProperty("program_digest")),
                ParseDigest(root.GetProperty("execution_digest")),
                ReceiptText(root.GetProperty("document_id"), 128),
                ReceiptText(root.GetProperty("document_revision_before"), 256),
                ReceiptText(root.GetProperty("document_revision_after"), 256),
                entities,
                layers);
            if (receipt.ReceiptId.Length != 52)
            {
                throw Invalid();
            }
            return receipt;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException or KeyNotFoundException)
        {
            throw Invalid();
        }
    }

    private static DurableEntityEvidence ParseEntity(JsonElement value)
    {
        EnsureExact(value, ["handle", "entity_type", "layer", "bounds"]);
        var bounds = value.GetProperty("bounds");
        EnsureExact(bounds, ["min_x", "min_y", "min_z", "max_x", "max_y", "max_z"]);
        var result = new CadBounds(
            ReceiptNumber(bounds, "min_x"),
            ReceiptNumber(bounds, "min_y"),
            ReceiptNumber(bounds, "min_z"),
            ReceiptNumber(bounds, "max_x"),
            ReceiptNumber(bounds, "max_y"),
            ReceiptNumber(bounds, "max_z"));
        if (result.MinX > result.MaxX || result.MinY > result.MaxY || result.MinZ > result.MaxZ)
        {
            throw Invalid();
        }
        return new DurableEntityEvidence(
            ReceiptText(value.GetProperty("handle"), 32),
            ReceiptText(value.GetProperty("entity_type"), 64),
            ReceiptText(value.GetProperty("layer"), 255),
            result);
    }

    private static double ReceiptNumber(JsonElement parent, string name)
    {
        if (!parent.GetProperty(name).TryGetDouble(out var value) ||
            !double.IsFinite(value) ||
            Math.Abs(value) > CadProgramV02Contract.MaxCoordinate)
        {
            throw Invalid();
        }
        return value;
    }

    private static string ParseDigest(JsonElement value)
    {
        var text = ReceiptText(value, 71);
        if (text.Length != 71 ||
            !text.StartsWith("sha256:", StringComparison.Ordinal) ||
            text[7..].Any(character => !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw Invalid();
        }
        return text;
    }

    private static string ReceiptText(JsonElement value, int maximum)
    {
        if (value.ValueKind != JsonValueKind.String ||
            value.GetString() is not { Length: > 0 } text ||
            text.Length > maximum ||
            text.Any(char.IsControl))
        {
            throw Invalid();
        }
        return text;
    }

    private static void EnsureExact(JsonElement value, IEnumerable<string> fields)
    {
        var expected = fields.ToHashSet(StringComparer.Ordinal);
        if (value.ValueKind != JsonValueKind.Object ||
            value.EnumerateObject().Count() != expected.Count ||
            value.EnumerateObject().Any(item => !expected.Contains(item.Name)))
        {
            throw Invalid();
        }
    }

    private static ProtocolValidationException Invalid() =>
        new("ledger_corrupt", "Drawing contains an invalid CAD Program receipt.");
}

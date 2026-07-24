using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AutocadMcp.Host.Core;

public static class CadProgramContract
{
    public const string RegistryVersion = "cad.program/0.1";
    public const int MaxOperations = 256;
    public const double MaxCoordinateMagnitude = 1_000_000_000d;
    public const double MaxRadius = 1_000_000_000d;
    public const int MaxTextLength = 4096;

    public static readonly IReadOnlySet<string> OperationIds = new HashSet<string>(StringComparer.Ordinal)
    {
        "cad.program.preview",
        "cad.program.commit",
        "cad.program.validate"
    };
}

public sealed record CadPoint(double X, double Y, double Z);

public abstract record CadCreateOperation(string Kind, string OperationId);

public sealed record EnsureLayerOperation(
    string OperationId,
    string Name,
    short? ColorIndex) : CadCreateOperation("ensure_layer", OperationId);

public sealed record CreateLineOperation(
    string OperationId,
    string Layer,
    CadPoint Start,
    CadPoint End) : CadCreateOperation("create_line", OperationId);

public sealed record CreateCircleOperation(
    string OperationId,
    string Layer,
    CadPoint Center,
    double Radius) : CadCreateOperation("create_circle", OperationId);

public sealed record CreatePolylineOperation(
    string OperationId,
    string Layer,
    IReadOnlyList<CadPoint> Vertices,
    bool Closed) : CadCreateOperation("create_polyline", OperationId);

public sealed record CreateTextOperation(
    string OperationId,
    string Layer,
    CadPoint Position,
    string Text,
    double Height,
    double RotationRadians) : CadCreateOperation("create_text", OperationId);

public sealed record CadRuntimeBinding(
    string RuntimeId,
    string HostFamily,
    string HostVersion,
    string PackageHash);

public sealed record CadProgram(
    string ProgramId,
    string IdempotencyKey,
    string DocumentId,
    long ExpectedRevision,
    string RegistryVersion,
    CadRuntimeBinding RuntimeBinding,
    IReadOnlyList<CadCreateOperation> Operations,
    string ProgramDigest);

public sealed record CadPreviewBinding(
    string ProgramDigest,
    string ExecutionDigest,
    long DocumentRevision,
    CadRuntimeBinding RuntimeBinding);

public sealed record CadProgramRequest(CadProgram Program, CadPreviewBinding? Preview);

public static class CadProgramParser
{
    private static readonly HashSet<string> ProgramFields =
    [
        "program_id", "idempotency_key", "document_id", "expected_revision",
        "registry_version", "runtime_binding", "operations"
    ];

    public static CadProgramRequest ParseRequest(string operationId, JsonElement arguments)
    {
        var allowed = operationId == "cad.program.commit"
            ? new HashSet<string> { "program", "preview" }
            : new HashSet<string> { "program" };
        EnsureExactObject(arguments, allowed, "CAD Program request");
        var programElement = arguments.GetProperty("program");
        var program = ParseProgram(programElement);
        CadPreviewBinding? preview = null;
        if (operationId == "cad.program.commit")
        {
            preview = ParsePreview(arguments.GetProperty("preview"));
            if (!FixedEquals(preview.ProgramDigest, program.ProgramDigest))
            {
                throw Invalid("Preview does not belong to this CAD Program.");
            }
        }
        return new CadProgramRequest(program, preview);
    }

    public static CadProgram ParseProgram(JsonElement value)
    {
        EnsureExactObject(value, ProgramFields, "CAD Program");
        var programId = Identifier(value, "program_id", 128);
        var idempotencyKey = Identifier(value, "idempotency_key", 128);
        var documentId = Identifier(value, "document_id", 128);
        var expectedRevision = PositiveInteger(value, "expected_revision");
        var registryVersion = RequiredString(value, "registry_version", 32);
        if (registryVersion != CadProgramContract.RegistryVersion)
        {
            throw new ProtocolValidationException("capability_missing", "Unsupported CAD Program registry version.");
        }

        var runtime = ParseRuntime(value.GetProperty("runtime_binding"));
        var operationValues = value.GetProperty("operations");
        if (operationValues.ValueKind != JsonValueKind.Array ||
            operationValues.GetArrayLength() is < 1 or > CadProgramContract.MaxOperations)
        {
            throw Invalid("CAD Program operation count is outside the allowed range.");
        }

        var operations = new List<CadCreateOperation>();
        var operationIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var operationValue in operationValues.EnumerateArray())
        {
            var operation = ParseOperation(operationValue);
            if (!operationIds.Add(operation.OperationId))
            {
                throw Invalid("CAD Program operation_id values must be unique.");
            }
            operations.Add(operation);
        }

        var digest = $"sha256:{CanonicalJson.Hash(value)}";
        return new CadProgram(
            programId, idempotencyKey, documentId, expectedRevision,
            registryVersion, runtime, operations, digest);
    }

    public static string BuildExecutionDigest(CadProgram program, CadRuntimeBinding actualRuntime)
    {
        var binding = string.Join(
            "\n",
            program.ProgramDigest,
            program.RegistryVersion,
            actualRuntime.RuntimeId,
            actualRuntime.HostFamily,
            actualRuntime.HostVersion,
            actualRuntime.PackageHash);
        return $"sha256:{Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(binding))).ToLowerInvariant()}";
    }

    public static void AssertRuntime(CadRuntimeBinding expected, CadRuntimeBinding actual)
    {
        if (expected.RuntimeId != "managed_dotnet" ||
            expected.RuntimeId != actual.RuntimeId ||
            expected.HostFamily != actual.HostFamily ||
            expected.HostVersion != actual.HostVersion ||
            !FixedEquals(expected.PackageHash, actual.PackageHash))
        {
            throw new ProtocolValidationException(
                "runtime_changed",
                "Runtime or package changed after the CAD Program was prepared.");
        }
    }

    private static CadCreateOperation ParseOperation(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw Invalid("CAD Program operation must be an object.");
        }
        var kind = RequiredString(value, "kind", 32);
        return kind switch
        {
            "ensure_layer" => ParseLayer(value),
            "create_line" => ParseLine(value),
            "create_circle" => ParseCircle(value),
            "create_polyline" => ParsePolyline(value),
            "create_text" => ParseText(value),
            _ => throw new ProtocolValidationException(
                "capability_missing",
                "CAD Program operation is not in the create-only allowlist.")
        };
    }

    private static EnsureLayerOperation ParseLayer(JsonElement value)
    {
        EnsureExactObject(value, ["kind", "operation_id", "name", "color_index"], "ensure_layer");
        var color = value.GetProperty("color_index");
        short? colorIndex = color.ValueKind == JsonValueKind.Null ? null : color.GetInt16();
        if (colorIndex is < 1 or > 255)
        {
            throw Invalid("Layer color_index must be between 1 and 255.");
        }
        return new EnsureLayerOperation(
            Identifier(value, "operation_id", 128),
            LayerName(value, "name"),
            colorIndex);
    }

    private static CreateLineOperation ParseLine(JsonElement value)
    {
        EnsureExactObject(value, ["kind", "operation_id", "layer", "start", "end"], "create_line");
        var start = Point(value, "start");
        var end = Point(value, "end");
        if (start == end)
        {
            throw Invalid("Line start and end must differ.");
        }
        return new CreateLineOperation(
            Identifier(value, "operation_id", 128), LayerName(value, "layer"), start, end);
    }

    private static CreateCircleOperation ParseCircle(JsonElement value)
    {
        EnsureExactObject(value, ["kind", "operation_id", "layer", "center", "radius"], "create_circle");
        var radius = FiniteNumber(value, "radius", 0, CadProgramContract.MaxRadius, exclusiveMinimum: true);
        return new CreateCircleOperation(
            Identifier(value, "operation_id", 128),
            LayerName(value, "layer"),
            Point(value, "center"),
            radius);
    }

    private static CreatePolylineOperation ParsePolyline(JsonElement value)
    {
        EnsureExactObject(value, ["kind", "operation_id", "layer", "vertices", "closed"], "create_polyline");
        var vertices = value.GetProperty("vertices");
        if (vertices.ValueKind != JsonValueKind.Array || vertices.GetArrayLength() is < 2 or > 4096)
        {
            throw Invalid("Polyline must contain between 2 and 4096 vertices.");
        }
        var points = vertices.EnumerateArray().Select(Point).ToArray();
        if (points.Distinct().Count() < 2)
        {
            throw Invalid("Polyline must contain at least two distinct vertices.");
        }
        return new CreatePolylineOperation(
            Identifier(value, "operation_id", 128),
            LayerName(value, "layer"),
            points,
            value.GetProperty("closed").GetBoolean());
    }

    private static CreateTextOperation ParseText(JsonElement value)
    {
        EnsureExactObject(
            value,
            ["kind", "operation_id", "layer", "position", "text", "height", "rotation_radians"],
            "create_text");
        var text = RequiredString(value, "text", CadProgramContract.MaxTextLength);
        if (text.Any(c => char.IsControl(c) && c is not '\r' and not '\n' and not '\t'))
        {
            throw Invalid("Text contains unsupported control characters.");
        }
        return new CreateTextOperation(
            Identifier(value, "operation_id", 128),
            LayerName(value, "layer"),
            Point(value, "position"),
            text,
            FiniteNumber(value, "height", 0, CadProgramContract.MaxRadius, exclusiveMinimum: true),
            FiniteNumber(value, "rotation_radians", -Math.PI * 2, Math.PI * 2));
    }

    private static CadRuntimeBinding ParseRuntime(JsonElement value)
    {
        EnsureExactObject(
            value,
            ["runtime_id", "host_family", "host_version", "package_hash"],
            "runtime binding");
        return new CadRuntimeBinding(
            RequiredString(value, "runtime_id", 32),
            RequiredString(value, "host_family", 32),
            RequiredString(value, "host_version", 32),
            Digest(value, "package_hash"));
    }

    private static CadPreviewBinding ParsePreview(JsonElement value)
    {
        EnsureExactObject(
            value,
            ["program_digest", "execution_digest", "document_revision", "runtime_binding"],
            "preview binding");
        return new CadPreviewBinding(
            Digest(value, "program_digest"),
            Digest(value, "execution_digest"),
            PositiveInteger(value, "document_revision"),
            ParseRuntime(value.GetProperty("runtime_binding")));
    }

    private static CadPoint Point(JsonElement parent, string property) => Point(parent.GetProperty(property));

    private static CadPoint Point(JsonElement value)
    {
        EnsureExactObject(value, ["x", "y", "z"], "point");
        return new CadPoint(
            Coordinate(value, "x"),
            Coordinate(value, "y"),
            Coordinate(value, "z"));
    }

    private static double Coordinate(JsonElement value, string name) =>
        FiniteNumber(
            value,
            name,
            -CadProgramContract.MaxCoordinateMagnitude,
            CadProgramContract.MaxCoordinateMagnitude);

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

    private static string LayerName(JsonElement value, string name)
    {
        var layer = RequiredString(value, name, 255).Trim();
        if (layer.Length == 0 || layer.IndexOfAny(['<', '>', '/', '\\', '"', ':', ';', '?', '*', '|', '=', '`']) >= 0)
        {
            throw Invalid("Layer name is invalid.");
        }
        return layer;
    }

    private static string Identifier(JsonElement value, string name, int maximum)
    {
        var identifier = RequiredString(value, name, maximum);
        if (identifier.Any(c => !(char.IsAsciiLetterOrDigit(c) || c is '.' or '_' or '-')))
        {
            throw Invalid($"{name} contains invalid characters.");
        }
        return identifier;
    }

    private static string Digest(JsonElement value, string name)
    {
        var digest = RequiredString(value, name, 71);
        if (digest.Length != 71 ||
            !digest.StartsWith("sha256:", StringComparison.Ordinal) ||
            digest[7..].Any(c => !Uri.IsHexDigit(c) || char.IsUpper(c)))
        {
            throw Invalid($"{name} must be a lowercase SHA-256 digest.");
        }
        return digest;
    }

    private static long PositiveInteger(JsonElement value, string name)
    {
        if (!value.TryGetProperty(name, out var property) ||
            !property.TryGetInt64(out var number) ||
            number < 1)
        {
            throw Invalid($"{name} must be a positive integer revision.");
        }
        return number;
    }

    private static string RequiredString(JsonElement value, string name, int maximum)
    {
        if (!value.TryGetProperty(name, out var property) ||
            property.ValueKind != JsonValueKind.String)
        {
            throw Invalid($"{name} is required.");
        }
        var text = property.GetString();
        if (string.IsNullOrEmpty(text) || text.Length > maximum)
        {
            throw Invalid($"{name} is invalid.");
        }
        return text;
    }

    private static void EnsureExactObject(
        JsonElement value,
        IEnumerable<string> fields,
        string subject)
    {
        var allowed = fields.ToHashSet(StringComparer.Ordinal);
        if (value.ValueKind != JsonValueKind.Object ||
            value.EnumerateObject().Any(property => !allowed.Contains(property.Name)) ||
            value.EnumerateObject().Count() != allowed.Count ||
            allowed.Any(field => !value.TryGetProperty(field, out _)))
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

public sealed record DurableProgramReceipt(
    string IdempotencyKey,
    string ProgramDigest,
    string ExecutionDigest,
    string CheckpointId)
{
    public const string RecordVersion = "cad.program.receipt/1";

    public string DictionaryKey
    {
        get
        {
            var digest = SHA256.HashData(Encoding.UTF8.GetBytes(IdempotencyKey));
            return $"AUTOCAD_MCP_PROGRAM_{Convert.ToHexString(digest).ToLowerInvariant()[..32]}";
        }
    }

    public string Serialize() => JsonSerializer.Serialize(
        new
        {
            record_version = RecordVersion,
            idempotency_key = IdempotencyKey,
            program_digest = ProgramDigest,
            execution_digest = ExecutionDigest,
            checkpoint_id = CheckpointId
        },
        HostProtocol.JsonOptions);

    public static DurableProgramReceipt Parse(string json)
    {
        if (json.Length is < 1 or > 2048)
        {
            throw InvalidReceipt();
        }
        try
        {
            using var document = JsonDocument.Parse(json, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 8
            });
            var root = document.RootElement;
            var fields = new HashSet<string>
            {
                "record_version", "idempotency_key", "program_digest",
                "execution_digest", "checkpoint_id"
            };
            if (root.ValueKind != JsonValueKind.Object ||
                root.EnumerateObject().Count() != fields.Count ||
                root.EnumerateObject().Any(property => !fields.Contains(property.Name)) ||
                root.GetProperty("record_version").GetString() != RecordVersion)
            {
                throw InvalidReceipt();
            }
            return new DurableProgramReceipt(
                ReceiptIdentifier(root, "idempotency_key", 128),
                ReceiptDigest(root, "program_digest"),
                ReceiptDigest(root, "execution_digest"),
                ReceiptIdentifier(root, "checkpoint_id", 128));
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException or KeyNotFoundException)
        {
            throw InvalidReceipt();
        }
    }

    private static string ReceiptIdentifier(JsonElement root, string name, int maximum)
    {
        var value = root.GetProperty(name).GetString();
        if (string.IsNullOrEmpty(value) ||
            value.Length > maximum ||
            value.Any(c => !(char.IsAsciiLetterOrDigit(c) || c is '.' or '_' or '-')))
        {
            throw InvalidReceipt();
        }
        return value;
    }

    private static string ReceiptDigest(JsonElement root, string name)
    {
        var value = root.GetProperty(name).GetString();
        if (value is null ||
            value.Length != 71 ||
            !value.StartsWith("sha256:", StringComparison.Ordinal) ||
            value[7..].Any(c => !Uri.IsHexDigit(c) || char.IsUpper(c)))
        {
            throw InvalidReceipt();
        }
        return value;
    }

    private static ProtocolValidationException InvalidReceipt() =>
        new("ledger_corrupt", "Drawing contains an invalid CAD Program receipt.");
}

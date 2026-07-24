using System.Buffers.Binary;
using System.Globalization;
using System.IO.Pipes;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutocadMcp.Host.Core;

public static class HostProtocol
{
    public const string Version = "cad.host/1";
    public const int MaxFrameBytes = 64 * 1024;
    public static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = false
    };
}

public sealed record HostEnvelope(
    string ProtocolVersion,
    string MessageType,
    string SessionId,
    string CommandId,
    long Sequence,
    DateTimeOffset DeadlineAt,
    string PayloadHash,
    JsonElement Payload);

public sealed record CommandRequest(
    string OperationId,
    int OperationVersion,
    string? DocumentId,
    JsonElement Arguments);

public sealed record RuntimeEvidence(
    string RuntimeId,
    string RuntimeRole,
    string HostFamily,
    string HostVersion,
    string PackageHash);

public sealed record OperationResult(
    string Status,
    string OperationId,
    object Result,
    RuntimeEvidence RuntimeEvidence);

public sealed record ProtocolError(string Status, string ErrorCode, string ErrorMessage);

public static class CanonicalJson
{
    private static readonly JsonWriterOptions WriterOptions = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static string Hash(JsonElement value)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, WriterOptions))
        {
            WriteCanonical(writer, value);
        }

        return Convert.ToHexString(SHA256.HashData(stream.ToArray())).ToLowerInvariant();
    }

    private static void WriteCanonical(Utf8JsonWriter writer, JsonElement value)
    {
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (var property in value.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteCanonical(writer, property.Value);
                }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in value.EnumerateArray())
                {
                    WriteCanonical(writer, item);
                }
                writer.WriteEndArray();
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(value.GetString());
                break;
            case JsonValueKind.Number:
                if (value.TryGetInt64(out var integer))
                {
                    writer.WriteNumberValue(integer);
                }
                else if (value.TryGetDecimal(out var decimalValue))
                {
                    writer.WriteRawValue(decimalValue.ToString(CultureInfo.InvariantCulture));
                }
                else
                {
                    throw new ProtocolValidationException("invalid_envelope", "Non-finite or unsupported number.");
                }
                break;
            case JsonValueKind.True:
                writer.WriteBooleanValue(true);
                break;
            case JsonValueKind.False:
                writer.WriteBooleanValue(false);
                break;
            case JsonValueKind.Null:
                writer.WriteNullValue();
                break;
            default:
                throw new ProtocolValidationException("invalid_envelope", "Unsupported JSON value.");
        }
    }
}

public sealed class ProtocolValidationException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

public static class EnvelopeValidator
{
    private static readonly HashSet<string> EnvelopeFields =
    [
        "protocol_version", "message_type", "session_id", "command_id",
        "sequence", "deadline_at", "payload_hash", "payload"
    ];

    public static HostEnvelope Parse(ReadOnlySpan<byte> json, DateTimeOffset now)
    {
        if (json.Length is 0 or > HostProtocol.MaxFrameBytes)
        {
            throw new ProtocolValidationException("invalid_envelope", "Frame size is outside the allowed range.");
        }

        try
        {
            using var document = JsonDocument.Parse(json.ToArray(), new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 32
            });
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                root.EnumerateObject().Any(p => !EnvelopeFields.Contains(p.Name)) ||
                root.EnumerateObject().Count() != EnvelopeFields.Count)
            {
                throw new ProtocolValidationException("invalid_envelope", "Envelope fields are missing or unknown.");
            }

            var protocol = RequiredString(root, "protocol_version", 32);
            if (protocol != HostProtocol.Version)
            {
                throw new ProtocolValidationException("protocol_mismatch", "Unsupported protocol version.");
            }

            var messageType = RequiredString(root, "message_type", 32);
            if (messageType is not ("handshake" or "command"))
            {
                throw new ProtocolValidationException("invalid_envelope", "Host accepts only handshake or command messages.");
            }

            var sessionId = RequiredIdentifier(root, "session_id");
            var commandId = RequiredIdentifier(root, "command_id");
            var sequence = root.GetProperty("sequence").GetInt64();
            if (sequence is < 0 or > 1_000_000_000)
            {
                throw new ProtocolValidationException("invalid_envelope", "Sequence is outside the allowed range.");
            }

            var deadlineText = RequiredString(root, "deadline_at", 64);
            if (!DateTimeOffset.TryParseExact(
                    deadlineText,
                    ["O", "yyyy-MM-dd'T'HH:mm:ssK"],
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.None,
                    out var deadline))
            {
                throw new ProtocolValidationException("invalid_envelope", "Deadline must be an ISO-8601 timestamp.");
            }

            if (deadline <= now)
            {
                throw new ProtocolValidationException("deadline_expired", "Command deadline has expired.");
            }

            var payloadHash = RequiredString(root, "payload_hash", 64);
            if (payloadHash.Length != 64 || payloadHash.Any(c => !Uri.IsHexDigit(c) || char.IsUpper(c)))
            {
                throw new ProtocolValidationException("invalid_envelope", "Payload hash must be lowercase SHA-256.");
            }

            var payload = root.GetProperty("payload");
            if (payload.ValueKind != JsonValueKind.Object)
            {
                throw new ProtocolValidationException("invalid_envelope", "Payload must be an object.");
            }

            if (!CryptographicOperations.FixedTimeEquals(
                    Encoding.ASCII.GetBytes(payloadHash),
                    Encoding.ASCII.GetBytes(CanonicalJson.Hash(payload))))
            {
                throw new ProtocolValidationException("payload_mismatch", "Payload hash does not match.");
            }

            return new HostEnvelope(
                protocol, messageType, sessionId, commandId, sequence, deadline,
                payloadHash, payload.Clone());
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Exception exception) when (exception is JsonException or InvalidOperationException or FormatException)
        {
            throw new ProtocolValidationException("invalid_envelope", "Malformed envelope.");
        }
    }

    public static CommandRequest ParseCommand(JsonElement payload)
    {
        var allowed = new HashSet<string> { "operation_id", "operation_version", "document_id", "arguments" };
        if (payload.EnumerateObject().Any(p => !allowed.Contains(p.Name)) ||
            !payload.TryGetProperty("operation_id", out var operation) ||
            !payload.TryGetProperty("operation_version", out var version) ||
            !payload.TryGetProperty("arguments", out var arguments))
        {
            throw new ProtocolValidationException("invalid_envelope", "Command fields are missing or unknown.");
        }

        var operationId = operation.GetString() ?? string.Empty;
        if (!OperationRegistry.IsAllowed(operationId))
        {
            throw new ProtocolValidationException("capability_missing", "Operation is not registered.");
        }

        if (version.GetInt32() != 1 || arguments.ValueKind != JsonValueKind.Object)
        {
            throw new ProtocolValidationException("invalid_envelope", "Unsupported operation version or arguments.");
        }

        string? documentId = null;
        if (payload.TryGetProperty("document_id", out var document) && document.ValueKind != JsonValueKind.Null)
        {
            documentId = document.GetString();
            if (documentId is null || documentId.Length > 128)
            {
                throw new ProtocolValidationException("invalid_envelope", "Invalid document ID.");
            }
        }

        ValidateArguments(operationId, arguments);
        return new CommandRequest(operationId, 1, documentId, arguments.Clone());
    }

    public static string ParseHandshakeNonce(JsonElement payload)
    {
        var allowed = new HashSet<string> { "session_nonce", "agent_version", "protocol_min", "protocol_max" };
        if (payload.EnumerateObject().Any(p => !allowed.Contains(p.Name)) ||
            payload.EnumerateObject().Count() != allowed.Count ||
            payload.GetProperty("protocol_min").GetString() != HostProtocol.Version ||
            payload.GetProperty("protocol_max").GetString() != HostProtocol.Version)
        {
            throw new ProtocolValidationException("protocol_mismatch", "Handshake is incomplete or incompatible.");
        }

        var nonce = payload.GetProperty("session_nonce").GetString();
        var agentVersion = payload.GetProperty("agent_version").GetString();
        if (nonce is null || nonce.Length is < 32 or > 256 ||
            agentVersion is null || agentVersion.Length is < 1 or > 64)
        {
            throw new ProtocolValidationException("invalid_envelope", "Invalid handshake values.");
        }

        return nonce;
    }

    private static void ValidateArguments(string operationId, JsonElement arguments)
    {
        if (CadProgramContract.OperationIds.Contains(operationId))
        {
            _ = CadProgramParser.ParseRequest(operationId, arguments);
            return;
        }
        if (operationId == "entity.snapshot.page")
        {
            _ = EntitySnapshotRequest.Parse(arguments);
            return;
        }
        if (operationId == "document.events.summary")
        {
            _ = DocumentEventsRequest.Parse(arguments);
            return;
        }

        var allowed = operationId == "host.health"
            ? new HashSet<string>()
            : new HashSet<string> { "include_layers", "max_layers" };
        if (arguments.EnumerateObject().Any(p => !allowed.Contains(p.Name)))
        {
            throw new ProtocolValidationException("invalid_envelope", "Unknown operation argument.");
        }

        if (arguments.TryGetProperty("include_layers", out var include) &&
            include.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
        {
            throw new ProtocolValidationException("invalid_envelope", "include_layers must be boolean.");
        }

        if (arguments.TryGetProperty("max_layers", out var max))
        {
            var value = max.GetInt32();
            if (value is < 0 or > 256)
            {
                throw new ProtocolValidationException("invalid_envelope", "max_layers is outside the allowed range.");
            }
        }
    }

    private static string RequiredIdentifier(JsonElement root, string name)
    {
        var value = RequiredString(root, name, 128);
        if (value.Any(c => !(char.IsAsciiLetterOrDigit(c) || c is '.' or '_' or '-')))
        {
            throw new ProtocolValidationException("invalid_envelope", $"{name} has invalid characters.");
        }
        return value;
    }

    private static string RequiredString(JsonElement root, string name, int maxLength)
    {
        var value = root.GetProperty(name).GetString();
        if (string.IsNullOrEmpty(value) || value.Length > maxLength)
        {
            throw new ProtocolValidationException("invalid_envelope", $"{name} is invalid.");
        }
        return value;
    }
}

public static class FrameCodec
{
    public static async Task<byte[]?> ReadAsync(Stream stream, CancellationToken cancellationToken)
    {
        var header = new byte[4];
        var headerRead = await ReadExactlyOrEofAsync(stream, header, cancellationToken).ConfigureAwait(false);
        if (!headerRead)
        {
            return null;
        }

        var length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length is <= 0 or > HostProtocol.MaxFrameBytes)
        {
            throw new ProtocolValidationException("invalid_envelope", "Frame length is outside the allowed range.");
        }

        var payload = new byte[length];
        await stream.ReadExactlyAsync(payload, cancellationToken).ConfigureAwait(false);
        return payload;
    }

    public static async Task WriteAsync(Stream stream, byte[] payload, CancellationToken cancellationToken)
    {
        if (payload.Length is <= 0 or > HostProtocol.MaxFrameBytes)
        {
            throw new ProtocolValidationException("invalid_envelope", "Response frame is outside the allowed range.");
        }

        var header = new byte[4];
        BinaryPrimitives.WriteInt32LittleEndian(header, payload.Length);
        await stream.WriteAsync(header, cancellationToken).ConfigureAwait(false);
        await stream.WriteAsync(payload, cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    private static async Task<bool> ReadExactlyOrEofAsync(Stream stream, byte[] buffer, CancellationToken cancellationToken)
    {
        var read = 0;
        while (read < buffer.Length)
        {
            var current = await stream.ReadAsync(buffer.AsMemory(read), cancellationToken).ConfigureAwait(false);
            if (current == 0)
            {
                if (read == 0)
                {
                    return false;
                }
                throw new EndOfStreamException("Truncated frame header.");
            }
            read += current;
        }
        return true;
    }
}

public sealed class ReplayGuard
{
    private readonly Dictionary<string, (string Hash, byte[] Response)> _entries = new(StringComparer.Ordinal);
    private readonly int _capacity;

    public ReplayGuard(int capacity = 256) => _capacity = capacity;

    public byte[]? Check(string commandId, string payloadHash)
    {
        if (!_entries.TryGetValue(commandId, out var entry))
        {
            return null;
        }
        if (!CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(entry.Hash),
                Encoding.ASCII.GetBytes(payloadHash)))
        {
            throw new ProtocolValidationException("duplicate_payload_mismatch", "Command ID was reused with another payload.");
        }
        return entry.Response;
    }

    public void Record(string commandId, string payloadHash, byte[] response)
    {
        if (_entries.Count >= _capacity)
        {
            _entries.Remove(_entries.Keys.First());
        }
        _entries[commandId] = (payloadHash, response);
    }
}

public interface IReadOnlyHostOperations
{
    Task<object> GetHandshakeEvidenceAsync(CancellationToken cancellationToken);
    Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken);
}

public sealed class OperationRegistry
{
    private static readonly HashSet<string> Allowed =
    [
        "host.health",
        "drawing.observe.summary",
        "entity.snapshot.page",
        "document.events.summary",
        "cad.program.preview",
        "cad.program.commit",
        "cad.program.validate"
    ];
    public IReadOnlyCollection<string> OperationIds => Allowed;

    public static bool IsAllowed(string operationId) => Allowed.Contains(operationId);
    public bool Contains(string operationId) => IsAllowed(operationId);
}

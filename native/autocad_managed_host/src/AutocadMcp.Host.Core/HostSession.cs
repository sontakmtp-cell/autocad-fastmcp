using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AutocadMcp.Host.Core;

public sealed class HostSession(
    byte[] sessionSecret,
    IReadOnlyHostOperations operations,
    RuntimeEvidence runtimeEvidence,
    Func<DateTimeOffset>? clock = null)
{
    private readonly ReplayGuard _replay = new();
    private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.UtcNow);
    private string? _sessionId;
    private long _lastSequence = -1;

    public async Task<byte[]> HandleAsync(byte[] requestBytes, CancellationToken cancellationToken)
    {
        HostEnvelope? request = null;
        try
        {
            request = EnvelopeValidator.Parse(requestBytes, _clock());
            using var deadlineCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            deadlineCancellation.CancelAfter(request.DeadlineAt - _clock());
            if (request.MessageType == "handshake")
            {
                return await HandleHandshakeAsync(request, deadlineCancellation.Token).ConfigureAwait(false);
            }

            EnsureSession(request);
            var replay = _replay.Check(request.CommandId, request.PayloadHash);
            if (replay is not null)
            {
                return MarkDuplicate(replay);
            }

            var command = EnvelopeValidator.ParseCommand(request.Payload);
            var result = await operations.ExecuteAsync(command, deadlineCancellation.Token).ConfigureAwait(false);
            var payload = new OperationResult("succeeded", command.OperationId, result, runtimeEvidence);
            var response = SerializeResponse(request, "result", payload);
            _replay.Record(request.CommandId, request.PayloadHash, response);
            _lastSequence = request.Sequence;
            return response;
        }
        catch (ProtocolValidationException exception)
        {
            return SerializeError(request, exception.Code, exception.Message);
        }
        catch (OperationCanceledException)
        {
            return SerializeError(request, "deadline_expired", "Operation was cancelled.");
        }
        catch (Exception exception)
        {
            return SerializeError(
                request,
                "internal_error",
                $"Managed Host operation failed ({exception.GetType().Name}).");
        }
    }

    private async Task<byte[]> HandleHandshakeAsync(
        HostEnvelope request,
        CancellationToken cancellationToken)
    {
        if (_sessionId is not null)
        {
            throw new ProtocolValidationException("session_rejected", "This pipe connection already completed its handshake.");
        }
        var nonce = EnvelopeValidator.ParseHandshakeNonce(request.Payload);
        var handshakeEvidence = await operations.GetHandshakeEvidenceAsync(cancellationToken).ConfigureAwait(false);
        var evidence = JsonSerializer.SerializeToNode(handshakeEvidence, HostProtocol.JsonOptions)
            ?? throw new InvalidOperationException("Missing handshake evidence.");
        evidence["selected_protocol"] = HostProtocol.Version;
        evidence["session_proof"] = CreateProof(request.SessionId, nonce);
        _sessionId = request.SessionId;
        _lastSequence = request.Sequence;
        return SerializeResponse(request, "handshake_result", evidence);
    }

    private void EnsureSession(HostEnvelope request)
    {
        if (_sessionId is null || request.SessionId != _sessionId)
        {
            throw new ProtocolValidationException("session_rejected", "Handshake is required for this pipe connection.");
        }
        if (request.Sequence <= _lastSequence)
        {
            var replay = _replay.Check(request.CommandId, request.PayloadHash);
            if (replay is null)
            {
                throw new ProtocolValidationException("session_rejected", "Sequence must increase.");
            }
        }
    }

    private string CreateProof(string sessionId, string nonce)
    {
        using var hmac = new HMACSHA256(sessionSecret);
        return Convert.ToHexString(hmac.ComputeHash(
            Encoding.UTF8.GetBytes($"{HostProtocol.Version}\n{sessionId}\n{nonce}"))).ToLowerInvariant();
    }

    private static byte[] MarkDuplicate(byte[] response)
    {
        var node = JsonNode.Parse(response)?.AsObject()
            ?? throw new InvalidOperationException("Invalid replay response.");
        node["payload"]!["status"] = "duplicate";
        var payloadJson = node["payload"]!.ToJsonString(HostProtocol.JsonOptions);
        using var document = JsonDocument.Parse(payloadJson);
        node["payload_hash"] = CanonicalJson.Hash(document.RootElement);
        return JsonSerializer.SerializeToUtf8Bytes(node, HostProtocol.JsonOptions);
    }

    private static byte[] SerializeResponse(HostEnvelope request, string messageType, object payload)
    {
        var payloadNode = JsonSerializer.SerializeToNode(payload, HostProtocol.JsonOptions)
            ?? throw new InvalidOperationException("Missing response payload.");
        using var document = JsonDocument.Parse(payloadNode.ToJsonString(HostProtocol.JsonOptions));
        var response = new JsonObject
        {
            ["protocol_version"] = HostProtocol.Version,
            ["message_type"] = messageType,
            ["session_id"] = request.SessionId,
            ["command_id"] = request.CommandId,
            ["sequence"] = request.Sequence,
            ["deadline_at"] = request.DeadlineAt.ToString("O"),
            ["payload_hash"] = CanonicalJson.Hash(document.RootElement),
            ["payload"] = payloadNode
        };
        return JsonSerializer.SerializeToUtf8Bytes(response, HostProtocol.JsonOptions);
    }

    private static byte[] SerializeError(HostEnvelope? request, string code, string message)
    {
        var fallbackDeadline = DateTimeOffset.UtcNow.AddSeconds(5);
        var envelope = request ?? new HostEnvelope(
            HostProtocol.Version, "error", "rejected", "rejected", 0,
            fallbackDeadline, new string('0', 64), default);
        return SerializeResponse(
            envelope,
            "error",
            new ProtocolError("failed", code, message.Length <= 2048 ? message : message[..2048]));
    }
}

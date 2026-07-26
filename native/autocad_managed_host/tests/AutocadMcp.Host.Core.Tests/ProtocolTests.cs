using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class ProtocolTests
{
    private static readonly DateTimeOffset Now = new(2026, 7, 24, 5, 0, 0, TimeSpan.Zero);

    [Fact]
    public void CanonicalHash_IsIndependentOfObjectKeyOrder()
    {
        using var first = JsonDocument.Parse("""{"b":[2,1],"a":"value"}""");
        using var second = JsonDocument.Parse("""{"a":"value","b":[2,1]}""");

        Assert.Equal(CanonicalJson.Hash(first.RootElement), CanonicalJson.Hash(second.RootElement));
    }

    [Fact]
    public void CanonicalHash_MatchesSharedUtf8Contract()
    {
        using var value = JsonDocument.Parse(
            """{"observed_at":"2026-07-24T16:54:09.4357622+00:00","label":"Bản vẽ"}""");

        Assert.Equal(
            "c09e55bd7452f5afc45e73394f14797b5b20dcd492e2bbb7efe9671d1d73cb91",
            CanonicalJson.Hash(value.RootElement));
    }

    [Fact]
    public void Parse_RejectsPayloadHashMismatch()
    {
        var request = BuildEnvelope("command", CommandPayload(), payloadHash: new string('0', 64));

        var error = Assert.Throws<ProtocolValidationException>(
            () => EnvelopeValidator.Parse(request, Now));

        Assert.Equal("payload_mismatch", error.Code);
    }

    [Fact]
    public void Parse_RejectsProtocolVersionMismatch()
    {
        var request = BuildEnvelope("command", CommandPayload(), protocol: "cad.host/2");

        var error = Assert.Throws<ProtocolValidationException>(
            () => EnvelopeValidator.Parse(request, Now));

        Assert.Equal("protocol_mismatch", error.Code);
    }

    [Fact]
    public void Registry_IsBoundedAndRejectsArbitraryExecution()
    {
        var registry = new OperationRegistry();

        Assert.Equal(
            [
                "cad.program.commit",
                "cad.program.preview",
                "cad.program.validate",
                "document.events.summary",
                "drawing.observe.summary",
                "entity.snapshot.page",
                "host.health"
            ],
            registry.OperationIds.OrderBy(item => item, StringComparer.Ordinal));
        Assert.False(registry.Contains("assembly.load"));
        Assert.False(registry.Contains("command.raw"));
        Assert.False(registry.Contains("lisp.evaluate"));
    }

    [Fact]
    public async Task Session_ReplaysSameCommandWithoutSecondExecution()
    {
        var operations = new FakeOperations();
        var session = CreateSession(operations);
        await session.HandleAsync(BuildEnvelope("handshake", HandshakePayload(), sequence: 0), default);
        var command = BuildEnvelope("command", CommandPayload(), sequence: 1);

        var first = await session.HandleAsync(command, default);
        var duplicate = await session.HandleAsync(command, default);

        Assert.Equal(1, operations.ExecutionCount);
        Assert.Equal("succeeded", ReadPayload(first).GetProperty("status").GetString());
        Assert.Equal("duplicate", ReadPayload(duplicate).GetProperty("status").GetString());
    }

    [Fact]
    public async Task Session_RejectsCommandIdReusedWithDifferentPayload()
    {
        var operations = new FakeOperations();
        var session = CreateSession(operations);
        await session.HandleAsync(BuildEnvelope("handshake", HandshakePayload(), sequence: 0), default);
        await session.HandleAsync(BuildEnvelope("command", CommandPayload(true), sequence: 1), default);

        var response = await session.HandleAsync(
            BuildEnvelope("command", CommandPayload(false), sequence: 2),
            default);

        Assert.Equal(
            "duplicate_payload_mismatch",
            ReadPayload(response).GetProperty("error_code").GetString());
        Assert.Equal(1, operations.ExecutionCount);
    }

    [Fact]
    public async Task Session_RequiresHandshake()
    {
        var session = CreateSession(new FakeOperations());

        var response = await session.HandleAsync(
            BuildEnvelope("command", CommandPayload(), sequence: 1),
            default);

        Assert.Equal("session_rejected", ReadPayload(response).GetProperty("error_code").GetString());
    }

    [Fact]
    public async Task FrameCodec_RejectsOversizedDeclaredFrame()
    {
        var bytes = BitConverter.GetBytes(HostProtocol.MaxFrameBytes + 1);
        await using var stream = new MemoryStream(bytes);

        var error = await Assert.ThrowsAsync<ProtocolValidationException>(
            () => FrameCodec.ReadAsync(stream, default));

        Assert.Equal("invalid_envelope", error.Code);
    }

    [Fact]
    public void EntitySnapshotRequest_EnforcesBudgetsAndFilters()
    {
        using var valid = JsonDocument.Parse(
            """{"cursor":2,"limit":50,"max_scan":1000,"expected_revision":7,"types":["LINE"],"layers":["DIM"],"space":"model"}""");

        var request = EntitySnapshotRequest.Parse(valid.RootElement);

        Assert.Equal(2, request.Cursor);
        Assert.Equal(50, request.Limit);
        Assert.Equal(7, request.ExpectedRevision);
        Assert.Contains("line", request.Types);
        Assert.Contains("dim", request.Layers);

        using var tooLarge = JsonDocument.Parse("""{"limit":201}""");
        var error = Assert.Throws<ProtocolValidationException>(
            () => EntitySnapshotRequest.Parse(tooLarge.RootElement));
        Assert.Equal("invalid_envelope", error.Code);
    }

    [Fact]
    public void DocumentRevision_AggregatesEventsAndRejectsStaleSnapshot()
    {
        var state = new DocumentRevisionState();
        var at = Now;
        state.Record(DocumentEventKind.ObjectModified, at, "1A", changesContent: true);
        state.Record(
            DocumentEventKind.ObjectModified,
            at.AddMilliseconds(10),
            "1B",
            changesContent: true);

        var page = state.ReadEvents(0, 10, at.AddSeconds(1));

        Assert.Equal(3, page.Revision.Revision);
        Assert.Equal(2, page.Revision.EventSequence);
        var batch = Assert.Single(page.Events);
        Assert.Equal(2, batch.Count);
        Assert.Equal(["1A", "1B"], batch.Handles);
        var error = Assert.Throws<ProtocolValidationException>(
            () => state.AssertRevision(2, at.AddSeconds(1)));
        Assert.Equal("stale_snapshot", error.Code);
    }

    [Fact]
    public void DocumentRevision_RejectsCursorOutsideRetainedWindow()
    {
        var state = new DocumentRevisionState();
        for (var index = 0; index < 260; index++)
        {
            state.Record(
                index % 2 == 0
                    ? DocumentEventKind.ObjectAppended
                    : DocumentEventKind.ObjectModified,
                Now.AddSeconds(index),
                index.ToString("X"),
                changesContent: true);
        }

        var error = Assert.Throws<ProtocolValidationException>(
            () => state.ReadEvents(1, 10, Now.AddMinutes(10)));

        Assert.Equal("stale_event_cursor", error.Code);
    }

    [Fact]
    public void DocumentRevision_SuppressesAbortedPreviewEvents()
    {
        var state = new DocumentRevisionState();

        using (state.SuppressChanges())
        {
            state.Record(
                DocumentEventKind.ObjectAppended,
                Now,
                "1A",
                changesContent: true);
        }

        var snapshot = state.Snapshot(Now.AddSeconds(1));
        Assert.Equal(1, snapshot.Revision);
        Assert.Equal(0, snapshot.EventSequence);
    }

    private static HostSession CreateSession(FakeOperations operations) =>
        new(
            Enumerable.Repeat((byte)0x2a, 32).ToArray(),
            operations,
            new RuntimeEvidence(
                "managed_dotnet",
                "primary",
                "R25",
                "0.1.0",
                $"sha256:{new string('a', 64)}"),
            () => Now);

    private static JsonObject HandshakePayload() => new()
    {
        ["session_nonce"] = "0123456789abcdef0123456789abcdef",
        ["agent_version"] = "0.1.0",
        ["protocol_min"] = HostProtocol.Version,
        ["protocol_max"] = HostProtocol.Version
    };

    private static JsonObject CommandPayload(bool includeLayers = true) => new()
    {
        ["operation_id"] = "drawing.observe.summary",
        ["operation_version"] = 1,
        ["document_id"] = "doc-test",
        ["arguments"] = new JsonObject
        {
            ["include_layers"] = includeLayers,
            ["max_layers"] = 10
        }
    };

    private static byte[] BuildEnvelope(
        string messageType,
        JsonObject payload,
        long sequence = 1,
        string protocol = HostProtocol.Version,
        string? payloadHash = null)
    {
        using var document = JsonDocument.Parse(payload.ToJsonString());
        var envelope = new JsonObject
        {
            ["protocol_version"] = protocol,
            ["message_type"] = messageType,
            ["session_id"] = "session-test",
            ["command_id"] = messageType == "handshake" ? "handshake-test" : "command-test",
            ["sequence"] = sequence,
            ["deadline_at"] = Now.AddMinutes(1).ToString("O"),
            ["payload_hash"] = payloadHash ?? CanonicalJson.Hash(document.RootElement),
            ["payload"] = payload
        };
        return JsonSerializer.SerializeToUtf8Bytes(envelope, HostProtocol.JsonOptions);
    }

    private static JsonElement ReadPayload(byte[] response)
    {
        using var document = JsonDocument.Parse(response);
        return document.RootElement.GetProperty("payload").Clone();
    }

    private sealed class FakeOperations : IReadOnlyHostOperations
    {
        public int ExecutionCount { get; private set; }

        public Task<object> GetHandshakeEvidenceAsync(CancellationToken cancellationToken) =>
            Task.FromResult<object>(new
            {
                host_family = "R25",
                host_version = "0.1.0",
                package_id = "autocad.managed_host.r25",
                package_version = "0.1.0",
                package_hash = $"sha256:{new string('a', 64)}",
                product = "AutoCAD Mechanical",
                edition = "full",
                release_year = 2025,
                series = "R25.0",
                active_document_id = "doc-test",
                capabilities = new[] { "host.health", "observe.summary" }
            });

        public Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken)
        {
            ExecutionCount++;
            return Task.FromResult<object>(new
            {
                document_id = "doc-test",
                document_name = "test.dwg",
                entity_count = 0,
                layer_count = 1,
                layers = new[] { "0" }
            });
        }
    }
}

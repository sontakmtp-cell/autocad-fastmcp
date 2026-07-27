using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class CadProgramV02Tests
{
    private const string GoldenDigest =
        "sha256:11ad7650bc721a2e109d14797d9c7d345d3e698e582ded8b8113594d4a277f60";

    [Fact]
    public void Parser_MatchesCrossLanguageGoldenAndExactSevenOperationRegistry()
    {
        using var document = JsonDocument.Parse(GoldenProgram().ToJsonString());

        var program = CadProgramV02Parser.ParseProgram(document.RootElement);
        var plan = CadProgramPlan.Build(program);

        Assert.Equal(GoldenDigest, program.ProgramDigest);
        Assert.Equal(
            CadProgramV02Contract.CreateOperationKinds,
            program.Operations.Select(item => item.Kind));
        Assert.Equal(6, plan.Entities.Count);
        Assert.Equal(
            ["LINE", "CIRCLE", "POLYLINE", "RECTANGLE", "TEXT", "DIMENSION_LINEAR"],
            plan.Entities.Select(item => item.EntityType));
        Assert.Equal("MCP-PHASE6", Assert.Single(plan.Layers));
    }

    [Fact]
    public void Parser_IsStrictAndRejectsArbitraryExecutionBoundary()
    {
        var program = GoldenProgram();
        program["path"] = @"C:\unsafe.dll";
        var unknown = ParseError(program);
        Assert.Equal("program_invalid", unknown.Code);

        program = GoldenProgram();
        program["operations"] = new JsonArray
        {
            new JsonObject
            {
                ["kind"] = "load_assembly",
                ["operation_id"] = "unsafe",
                ["path"] = @"C:\unsafe.dll"
            }
        };
        var arbitrary = ParseError(program);
        Assert.Equal("capability_missing", arbitrary.Code);
    }

    [Fact]
    public void Parser_RejectsForwardAndWrongTypedLayerReferences()
    {
        var program = GoldenProgram();
        var operations = program["operations"]!.AsArray();
        operations[1]!["layer"] = new JsonObject
        {
            ["operation_id"] = "layer-later",
            ["output"] = "layer"
        };
        operations.Add(new JsonObject
        {
            ["kind"] = "ensure_layer",
            ["operation_id"] = "layer-later",
            ["name"] = "LATER"
        });

        var error = ParseError(program);

        Assert.Equal("program_invalid", error.Code);
        Assert.Contains("earlier ensure_layer", error.Message);
    }

    [Fact]
    public void Parser_EnforcesEntityVertexTextAndCoordinateBudgets()
    {
        var program = GoldenProgram();
        program["budgets"]!["max_entities"] = 5;
        Assert.Equal("program_invalid", ParseError(program).Code);

        program = GoldenProgram();
        program["budgets"]!["max_vertices"] = 3;
        Assert.Equal("program_invalid", ParseError(program).Code);

        program = GoldenProgram();
        program["budgets"]!["max_text_bytes"] = 2;
        Assert.Equal("program_invalid", ParseError(program).Code);

        program = GoldenProgram();
        program["budgets"]!["max_coordinate_abs"] = 10.0;
        Assert.Equal("program_invalid", ParseError(program).Code);
    }

    [Fact]
    public void Request_BindsProgramDocumentDigestAndAllHostPins()
    {
        var program = GoldenProgram();
        var arguments = PreviewArguments(program);
        using var document = JsonDocument.Parse(arguments.ToJsonString());

        var request = CadProgramV02Parser.ParseRequest(
            "cad.program.preview",
            document.RootElement);
        CadProgramV02Parser.AssertHostBinding(request.ExecutionBinding, HostBinding());

        Assert.Equal(GoldenDigest, request.Program!.ProgramDigest);

        arguments["execution_binding"]!["package_hash"] = Digest('9');
        using var changed = JsonDocument.Parse(arguments.ToJsonString());
        request = CadProgramV02Parser.ParseRequest("cad.program.preview", changed.RootElement);
        var error = Assert.Throws<ProtocolValidationException>(
            () => CadProgramV02Parser.AssertHostBinding(request.ExecutionBinding, HostBinding()));
        Assert.Equal("runtime_changed", error.Code);
    }

    [Fact]
    public void Request_CarriesExactPreviewReceiptAndValidationIds()
    {
        var previewArguments = PreviewArguments(GoldenProgram());
        using var previewDocument = JsonDocument.Parse(previewArguments.ToJsonString());
        var previewRequest = CadProgramV02Parser.ParseRequest(
            "cad.program.preview",
            previewDocument.RootElement);
        Assert.Equal("preview-001", previewRequest.PreviewId);
        Assert.Equal("2026-07-26T01:15:00+00:00", previewRequest.PreviewExpiresAt);

        var previewDigest = CadProgramV02Parser.BuildPreviewDigest(
            previewRequest.PreviewId!,
            previewRequest.Program!,
            previewRequest.ExecutionBinding);
        var receiptId = DurableProgramReceiptV02.BuildReceiptId(previewRequest.PreviewId!);
        Assert.Equal(
            "sha256:85da3cf8f778c421b242cb37ccaf2d326ac46e0dc92720b749dc1272da7e7c91",
            previewDigest);
        Assert.Equal(
            "AUTOCAD_MCP_PROGRAM_e3e78279e01c532929adc6d8515a6b83",
            receiptId);
        var commitArguments = new JsonObject
        {
            ["program"] = GoldenProgram(),
            ["execution_binding"] = Binding(),
            ["preview_binding"] = new JsonObject
            {
                ["preview_id"] = previewRequest.PreviewId,
                ["preview_digest"] = previewDigest
            },
            ["receipt_id"] = receiptId
        };
        using var commitDocument = JsonDocument.Parse(commitArguments.ToJsonString());
        var commitRequest = CadProgramV02Parser.ParseRequest(
            "cad.program.commit",
            commitDocument.RootElement);
        Assert.Equal("preview-001", commitRequest.Preview!.PreviewId);
        Assert.Equal(receiptId, commitRequest.ReceiptId);

        var validateArguments = new JsonObject
        {
            ["execution_binding"] = Binding(),
            ["validation"] = new JsonObject
            {
                ["validation_id"] = "validation-001",
                ["receipt_id"] = receiptId
            }
        };
        using var validateDocument = JsonDocument.Parse(validateArguments.ToJsonString());
        var validateRequest = CadProgramV02Parser.ParseRequest(
            "cad.program.validate",
            validateDocument.RootElement);
        Assert.Equal("validation-001", validateRequest.Validation!.ValidationId);
        Assert.Equal(receiptId, validateRequest.Validation.ReceiptId);
    }

    [Fact]
    public void PreviewRequest_RequiresAnExactTimezoneAwareGatewayExpiry()
    {
        var missing = PreviewArguments(GoldenProgram());
        missing.Remove("expires_at");
        using var missingDocument = JsonDocument.Parse(missing.ToJsonString());
        var missingError = Assert.Throws<ProtocolValidationException>(
            () => CadProgramV02Parser.ParseRequest(
                "cad.program.preview",
                missingDocument.RootElement));
        Assert.Equal("program_invalid", missingError.Code);

        var localTime = PreviewArguments(GoldenProgram());
        localTime["expires_at"] = "2026-07-26T01:15:00";
        using var localDocument = JsonDocument.Parse(localTime.ToJsonString());
        var localError = Assert.Throws<ProtocolValidationException>(
            () => CadProgramV02Parser.ParseRequest(
                "cad.program.preview",
                localDocument.RootElement));
        Assert.Equal("program_invalid", localError.Code);
    }

    [Fact]
    public void HostCommandParser_AcceptsOnlyTypedV02ProgramArguments()
    {
        var payload = new JsonObject
        {
            ["operation_id"] = "cad.program.preview",
            ["operation_version"] = 1,
            ["document_id"] = "document-001",
            ["arguments"] = PreviewArguments(GoldenProgram())
        };
        using var document = JsonDocument.Parse(payload.ToJsonString());

        var command = EnvelopeValidator.ParseCommand(document.RootElement);

        Assert.Equal("cad.program.preview", command.OperationId);
        Assert.Equal("document-001", command.DocumentId);

        payload["arguments"]!["path"] = @"C:\unsafe.dll";
        using var injected = JsonDocument.Parse(payload.ToJsonString());
        var error = Assert.Throws<ProtocolValidationException>(
            () => EnvelopeValidator.ParseCommand(injected.RootElement));
        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void Request_RejectsWrongRegistryAndProgramDigest()
    {
        var arguments = PreviewArguments(GoldenProgram());
        arguments["execution_binding"]!["operation_registry_hash"] = Digest('9');
        using var wrongRegistry = JsonDocument.Parse(arguments.ToJsonString());
        var request = CadProgramV02Parser.ParseRequest(
            "cad.program.preview",
            wrongRegistry.RootElement);
        var registryError = Assert.Throws<ProtocolValidationException>(
            () => CadProgramV02Parser.AssertHostBinding(request.ExecutionBinding, HostBinding()));
        Assert.Equal("capability_missing", registryError.Code);

        arguments = PreviewArguments(GoldenProgram());
        arguments["execution_binding"]!["program_digest"] = Digest('9');
        using var wrongDigest = JsonDocument.Parse(arguments.ToJsonString());
        var digestError = Assert.Throws<ProtocolValidationException>(
            () => CadProgramV02Parser.ParseRequest("cad.program.preview", wrongDigest.RootElement));
        Assert.Equal("program_invalid", digestError.Code);
    }

    [Fact]
    public void Admission_FailsClosedForBusyModalAndDocumentMismatch()
    {
        var now = new DateTimeOffset(2026, 7, 26, 0, 0, 0, TimeSpan.Zero);
        Assert.Equal(
            "autocad_busy",
            Assert.Throws<ProtocolValidationException>(
                () => CadHostAdmission.AssertCommandState(1)).Code);
        Assert.Equal(
            "modal_dialog_active",
            Assert.Throws<ProtocolValidationException>(
                () => CadHostAdmission.AssertCommandState(8)).Code);
        Assert.Equal(
            "active_document_changed",
            Assert.Throws<ProtocolValidationException>(
                () => CadHostAdmission.AssertDocument(
                    "doc-a", "doc-a", "doc-b", "doc-a")).Code);
        Assert.Equal(
            "deadline_expired",
            Assert.Throws<ProtocolValidationException>(
                () => CadHostAdmission.AssertDeadline(
                    now.AddSeconds(121), now, 120)).Code);
        CadHostAdmission.AssertCommandState(0);
        CadHostAdmission.AssertDocument("doc-a", "doc-a", "doc-a", "doc-a");
        CadHostAdmission.AssertDeadline(now.AddSeconds(120), now, 120);
    }

    [Fact]
    public void DocumentIdentity_IsStableAcrossHostRestartAndCaseNormalized()
    {
        var first = StableDocumentIdentity.FromDatabaseFingerprint(
            "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE");
        var reopened = StableDocumentIdentity.FromDatabaseFingerprint(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");

        Assert.Equal(first, reopened);
        Assert.StartsWith("doc-", first);
        Assert.Equal(28, first.Length);
        Assert.Equal(
            "program_invalid",
            Assert.Throws<ProtocolValidationException>(
                () => StableDocumentIdentity.FromDatabaseFingerprint("unavailable")).Code);
    }

    [Fact]
    public void PreviewLedger_RequiresExactUnexpiredProgramAndRuntimeBinding()
    {
        var now = new DateTimeOffset(2026, 7, 26, 0, 0, 0, TimeSpan.Zero);
        var ledger = new CadPreviewLedger();
        var record = new CadPreviewRecord(
            "preview-1",
            Digest('1'),
            GoldenDigest,
            Digest('2'),
            now.AddMinutes(5));
        ledger.Add(record);

        Assert.Equal(
            record,
            ledger.Require(
                new CadPreviewReference(record.PreviewId, record.PreviewDigest),
                record.ProgramDigest,
                record.BindingDigest,
                now));
        Assert.Equal(
            "runtime_changed",
            Assert.Throws<ProtocolValidationException>(
                () => ledger.Require(
                    new CadPreviewReference(record.PreviewId, Digest('9')),
                    record.ProgramDigest,
                    record.BindingDigest,
                    now)).Code);
        Assert.Equal(
            "preview_required",
            Assert.Throws<ProtocolValidationException>(
                () => ledger.Require(
                    new CadPreviewReference(record.PreviewId, record.PreviewDigest),
                    record.ProgramDigest,
                    record.BindingDigest,
                    now.AddMinutes(6))).Code);
    }

    [Fact]
    public void DurableReceipt_RoundTripsGeometryAndDetectsCorruption()
    {
        var receipt = Receipt();

        var restored = DurableProgramReceiptV02.Parse(receipt.Serialize());

        Assert.Equal(receipt.IdempotencyKey, restored.IdempotencyKey);
        Assert.Equal(receipt.Entities, restored.Entities);
        Assert.Equal(receipt.Layers, restored.Layers);
        Assert.StartsWith("AUTOCAD_MCP_PROGRAM_", restored.ReceiptId);
        Assert.StartsWith("sha256:", restored.ReceiptDigest);

        var node = JsonNode.Parse(receipt.Serialize())!.AsObject();
        node["arbitrary_code"] = "System.Reflection";
        var error = Assert.Throws<ProtocolValidationException>(
            () => DurableProgramReceiptV02.Parse(node.ToJsonString()));
        Assert.Equal("ledger_corrupt", error.Code);
    }

    [Fact]
    public void TransactionModel_PreviewHasNoEffectCommitIsExactAndExceptionRollsBack()
    {
        var program = ParseGolden();
        var plan = CadProgramPlan.Build(program);
        var model = new GeometryTransactionModel();

        model.Preview(plan);
        Assert.Empty(model.Entities);
        Assert.Empty(model.Layers);

        model.Commit("commit-1", plan);
        Assert.Equal(6, model.Entities.Count);
        Assert.Single(model.Layers);
        model.Commit("commit-1", plan);
        Assert.Equal(6, model.Entities.Count);

        Assert.Throws<InvalidOperationException>(
            () => model.Commit("commit-failure", plan, throwAfter: 2));
        Assert.Equal(6, model.Entities.Count);
        Assert.Single(model.Layers);
    }

    private static ProtocolValidationException ParseError(JsonObject program)
    {
        using var document = JsonDocument.Parse(program.ToJsonString());
        return Assert.Throws<ProtocolValidationException>(
            () => CadProgramV02Parser.ParseProgram(document.RootElement));
    }

    private static CadProgramV02 ParseGolden()
    {
        using var document = JsonDocument.Parse(GoldenProgram().ToJsonString());
        return CadProgramV02Parser.ParseProgram(document.RootElement);
    }

    private static JsonObject PreviewArguments(JsonObject program) => new()
    {
        ["program"] = program,
        ["execution_binding"] = Binding(),
        ["preview_id"] = "preview-001",
        ["expires_at"] = "2026-07-26T01:15:00+00:00"
    };

    private static JsonObject Binding() => new()
    {
        ["program_digest"] = GoldenDigest,
        ["execution_digest"] = Digest('1'),
        ["document_id"] = "document-001",
        ["document_revision"] = "revision-007",
        ["runtime_id"] = "managed_dotnet",
        ["runtime_role"] = "primary",
        ["host_family"] = "R25",
        ["host_version"] = "0.2.0",
        ["package_id"] = "autocad.managed_host.r25",
        ["package_version"] = "0.2.0",
        ["package_hash"] = Digest('2'),
        ["capability_manifest_hash"] = Digest('3'),
        ["operation_registry_version"] = CadProgramV02Contract.RegistryVersion,
        ["operation_registry_hash"] = CadProgramV02Contract.RegistryDigest,
        ["policy_version"] = "phase6-policy/1"
    };

    private static CadHostBinding HostBinding() => new(
        "managed_dotnet",
        "R25",
        "0.2.0",
        "autocad.managed_host.r25",
        "0.2.0",
        Digest('2'));

    private static DurableProgramReceiptV02 Receipt() => new(
        "commit-1",
        GoldenDigest,
        Digest('1'),
        "document-001",
        "7",
        "8",
        [
            new DurableEntityEvidence(
                "1A",
                "LINE",
                "MCP-PHASE6",
                new CadBounds(0, 0, 0, 100, 0, 0))
        ],
        ["MCP-PHASE6"]);

    private static string Digest(char value) => $"sha256:{new string(value, 64)}";

    private static JsonObject GoldenProgram() => JsonNode.Parse(
        """
        {
          "schema_version": "cad.program/0.2",
          "registry_version": "cad.program/0.2",
          "program_id": "program-001",
          "program_revision": 1,
          "device_id": "device-001",
          "source_snapshot_id": "snapshot-001",
          "document_id": "document-001",
          "expected_document_revision": "revision-007",
          "operations": [
            {"kind":"ensure_layer","operation_id":"layer-main","name":"MCP-PHASE6","color_index":3},
            {"kind":"create_line","operation_id":"line-001","layer":{"operation_id":"layer-main","output":"layer"},"start":{"x":0.0,"y":0.0,"z":0.0},"end":{"x":100.0,"y":0.0,"z":0.0}},
            {"kind":"create_circle","operation_id":"circle-001","layer":{"operation_id":"layer-main","output":"layer"},"center":{"x":25.0,"y":25.0,"z":0.0},"radius":5.0},
            {"kind":"create_polyline","operation_id":"polyline-001","layer":{"operation_id":"layer-main","output":"layer"},"vertices":[{"x":0.0,"y":0.0,"z":0.0},{"x":20.0,"y":0.0,"z":0.0},{"x":20.0,"y":20.0,"z":0.0}],"closed":true},
            {"kind":"create_rectangle","operation_id":"rectangle-001","layer":{"operation_id":"layer-main","output":"layer"},"first_corner":{"x":0.0,"y":0.0,"z":0.0},"opposite_corner":{"x":40.0,"y":30.0,"z":0.0}},
            {"kind":"create_text","operation_id":"text-001","layer":{"operation_id":"layer-main","output":"layer"},"position":{"x":5.0,"y":5.0,"z":0.0},"text":"Phase 6","height":2.5,"rotation_radians":0.0},
            {"kind":"create_dimension_linear","operation_id":"dimension-001","layer":{"operation_id":"layer-main","output":"layer"},"extension_line1_point":{"x":0.0,"y":0.0,"z":0.0},"extension_line2_point":{"x":40.0,"y":0.0,"z":0.0},"dimension_line_point":{"x":20.0,"y":-5.0,"z":0.0}}
          ],
          "preconditions": [
            {"kind":"document_revision_equals","document_id":"document-001","expected_document_revision":"revision-007"}
          ],
          "postconditions": [
            {"kind":"entity_count","expected_created":6},
            {"kind":"layer_exists","layer":{"operation_id":"layer-main","output":"layer"}}
          ],
          "budgets": {
            "max_operations":256,
            "max_entities":256,
            "max_layers":64,
            "max_vertices":4096,
            "max_text_bytes":65536,
            "max_payload_bytes":1048576,
            "max_result_bytes":1048576,
            "max_artifact_bytes":5242880,
            "max_coordinate_abs":1000000000.0,
            "max_radius":1000000000.0,
            "max_text_height":1000000000.0,
            "execution_deadline_seconds":120,
            "preview_ttl_seconds":900
          }
        }
        """)!.AsObject();

    private sealed class GeometryTransactionModel
    {
        private readonly HashSet<string> _commits = new(StringComparer.Ordinal);
        public List<CadPlannedEntity> Entities { get; } = [];
        public List<string> Layers { get; } = [];

        public void Preview(CadProgramPlan plan)
        {
            _ = ApplyToCopies(plan, int.MaxValue);
        }

        public void Commit(string key, CadProgramPlan plan, int throwAfter = int.MaxValue)
        {
            if (_commits.Contains(key))
            {
                return;
            }
            var (entities, layers) = ApplyToCopies(plan, throwAfter);
            Entities.Clear();
            Entities.AddRange(entities);
            Layers.Clear();
            Layers.AddRange(layers);
            _commits.Add(key);
        }

        private (List<CadPlannedEntity>, List<string>) ApplyToCopies(
            CadProgramPlan plan,
            int throwAfter)
        {
            var entities = Entities.ToList();
            var layers = Layers.ToList();
            layers.AddRange(plan.Layers.Where(layer => !layers.Contains(layer, StringComparer.OrdinalIgnoreCase)));
            foreach (var entity in plan.Entities)
            {
                if (entities.Count - Entities.Count == throwAfter)
                {
                    throw new InvalidOperationException("Injected transaction failure.");
                }
                entities.Add(entity);
            }
            return (entities, layers);
        }
    }
}

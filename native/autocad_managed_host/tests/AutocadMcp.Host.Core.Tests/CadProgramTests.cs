using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class CadProgramTests
{
    [Fact]
    public void Parser_AcceptsOnlyCreateOnlyPrimitiveRegistry()
    {
        using var document = BuildRequest();

        var request = CadProgramParser.ParseRequest("cad.program.preview", document.RootElement);

        Assert.Equal(CadProgramContract.RegistryVersion, request.Program.RegistryVersion);
        Assert.Collection(
            request.Program.Operations,
            item => Assert.IsType<EnsureLayerOperation>(item),
            item => Assert.IsType<CreateLineOperation>(item),
            item => Assert.IsType<CreateCircleOperation>(item),
            item => Assert.IsType<CreatePolylineOperation>(item),
            item => Assert.IsType<CreateTextOperation>(item));
        Assert.StartsWith("sha256:", request.Program.ProgramDigest);
    }

    [Theory]
    [InlineData("run_command")]
    [InlineData("load_assembly")]
    [InlineData("execute_lisp")]
    [InlineData("erase_entity")]
    public void Parser_RejectsArbitraryOrDestructiveOperations(string kind)
    {
        var root = ProgramNode();
        root["operations"] = new JsonArray
        {
            new JsonObject
            {
                ["kind"] = kind,
                ["operation_id"] = "op-unsafe"
            }
        };
        using var document = JsonDocument.Parse(
            new JsonObject { ["program"] = root }.ToJsonString());

        var error = Assert.Throws<ProtocolValidationException>(
            () => CadProgramParser.ParseRequest("cad.program.preview", document.RootElement));

        Assert.Equal("capability_missing", error.Code);
    }

    [Fact]
    public void Parser_RejectsNonFiniteOrOutOfBoundsCoordinates()
    {
        var root = ProgramNode();
        root["operations"] = new JsonArray
        {
            new JsonObject
            {
                ["kind"] = "create_line",
                ["operation_id"] = "line-1",
                ["layer"] = "0",
                ["start"] = Point(1_000_000_001, 0, 0),
                ["end"] = Point(1, 1, 0)
            }
        };
        using var document = JsonDocument.Parse(
            new JsonObject { ["program"] = root }.ToJsonString());

        var error = Assert.Throws<ProtocolValidationException>(
            () => CadProgramParser.ParseRequest("cad.program.preview", document.RootElement));

        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void Commit_RequiresExactProgramAndPreviewDigest()
    {
        var program = ProgramNode();
        using var parsedProgram = JsonDocument.Parse(program.ToJsonString());
        var digest = $"sha256:{CanonicalJson.Hash(parsedProgram.RootElement)}";
        var request = new JsonObject
        {
            ["program"] = program,
            ["preview"] = new JsonObject
            {
                ["program_digest"] = $"sha256:{new string('f', 64)}",
                ["execution_digest"] = $"sha256:{new string('e', 64)}",
                ["document_revision"] = 1,
                ["runtime_binding"] = RuntimeBinding()
            }
        };
        using var document = JsonDocument.Parse(request.ToJsonString());

        var error = Assert.Throws<ProtocolValidationException>(
            () => CadProgramParser.ParseRequest("cad.program.commit", document.RootElement));

        Assert.NotEqual($"sha256:{new string('f', 64)}", digest);
        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void ExecutionDigest_BindsProgramRegistryAndRuntimePackage()
    {
        using var document = BuildRequest();
        var program = CadProgramParser.ParseRequest(
            "cad.program.preview",
            document.RootElement).Program;
        var runtime = new CadRuntimeBinding(
            "managed_dotnet", "R25", "0.1.0", $"sha256:{new string('a', 64)}");
        var changedPackage = runtime with { PackageHash = $"sha256:{new string('b', 64)}" };

        var first = CadProgramParser.BuildExecutionDigest(program, runtime);
        var second = CadProgramParser.BuildExecutionDigest(program, changedPackage);

        Assert.NotEqual(first, second);
    }

    [Fact]
    public void RuntimePinning_RejectsRuntimeOrPackageChange()
    {
        var expected = new CadRuntimeBinding(
            "managed_dotnet", "R25", "0.1.0", $"sha256:{new string('a', 64)}");
        var actual = expected with { HostVersion = "0.2.0" };

        var error = Assert.Throws<ProtocolValidationException>(
            () => CadProgramParser.AssertRuntime(expected, actual));

        Assert.Equal("runtime_changed", error.Code);
    }

    [Fact]
    public void DurableReceipt_RoundTripsAndUsesBoundedOpaqueDictionaryKey()
    {
        var receipt = new DurableProgramReceipt(
            "idempotency-test",
            $"sha256:{new string('a', 64)}",
            $"sha256:{new string('b', 64)}",
            "checkpoint-test");

        var restored = DurableProgramReceipt.Parse(receipt.Serialize());

        Assert.Equal(receipt, restored);
        Assert.StartsWith("AUTOCAD_MCP_PROGRAM_", receipt.DictionaryKey);
        Assert.Equal(52, receipt.DictionaryKey.Length);
        Assert.DoesNotContain(receipt.IdempotencyKey, receipt.DictionaryKey);
    }

    [Fact]
    public void DurableReceipt_RejectsUnknownFieldsOrCorruptDigest()
    {
        var invalid = $$"""
        {
          "record_version": "{{DurableProgramReceipt.RecordVersion}}",
          "idempotency_key": "idempotency-test",
          "program_digest": "sha256:bad",
          "execution_digest": "sha256:{{new string('b', 64)}}",
          "checkpoint_id": "checkpoint-test",
          "unexpected": true
        }
        """;

        var error = Assert.Throws<ProtocolValidationException>(
            () => DurableProgramReceipt.Parse(invalid));

        Assert.Equal("ledger_corrupt", error.Code);
    }

    private static JsonDocument BuildRequest() =>
        JsonDocument.Parse(new JsonObject { ["program"] = ProgramNode() }.ToJsonString());

    private static JsonObject ProgramNode() => new()
    {
        ["program_id"] = "program-test",
        ["idempotency_key"] = "idempotency-test",
        ["document_id"] = "doc-test",
        ["expected_revision"] = 1,
        ["registry_version"] = CadProgramContract.RegistryVersion,
        ["runtime_binding"] = RuntimeBinding(),
        ["operations"] = new JsonArray
        {
            new JsonObject
            {
                ["kind"] = "ensure_layer",
                ["operation_id"] = "layer-1",
                ["name"] = "MCP-TEST",
                ["color_index"] = 3
            },
            new JsonObject
            {
                ["kind"] = "create_line",
                ["operation_id"] = "line-1",
                ["layer"] = "MCP-TEST",
                ["start"] = Point(0, 0, 0),
                ["end"] = Point(10, 10, 0)
            },
            new JsonObject
            {
                ["kind"] = "create_circle",
                ["operation_id"] = "circle-1",
                ["layer"] = "MCP-TEST",
                ["center"] = Point(5, 5, 0),
                ["radius"] = 2
            },
            new JsonObject
            {
                ["kind"] = "create_polyline",
                ["operation_id"] = "polyline-1",
                ["layer"] = "MCP-TEST",
                ["vertices"] = new JsonArray(Point(0, 0, 0), Point(5, 0, 0), Point(5, 5, 0)),
                ["closed"] = true
            },
            new JsonObject
            {
                ["kind"] = "create_text",
                ["operation_id"] = "text-1",
                ["layer"] = "MCP-TEST",
                ["position"] = Point(1, 1, 0),
                ["text"] = "Phase 5",
                ["height"] = 2.5,
                ["rotation_radians"] = 0
            }
        }
    };

    private static JsonObject RuntimeBinding() => new()
    {
        ["runtime_id"] = "managed_dotnet",
        ["host_family"] = "R25",
        ["host_version"] = "0.1.0",
        ["package_hash"] = $"sha256:{new string('a', 64)}"
    };

    private static JsonObject Point(double x, double y, double z) => new()
    {
        ["x"] = x,
        ["y"] = y,
        ["z"] = z
    };
}

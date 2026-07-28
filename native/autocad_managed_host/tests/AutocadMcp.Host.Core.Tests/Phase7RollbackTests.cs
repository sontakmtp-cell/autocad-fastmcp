using System.Text.Json;
using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class Phase7RollbackTests
{
    [Fact]
    public void Checkpoint_RoundTripsCanonicalFingerprintAndExactPins()
    {
        var checkpoint = Checkpoint();

        var restored = CadRollbackCheckpointV1.Parse(checkpoint.Serialize());

        Assert.Equal(Phase7RollbackContract.CheckpointVersion, restored.SchemaVersion);
        Assert.Equal(checkpoint.CheckpointDigest, restored.CheckpointDigest);
        Assert.Equal(checkpoint.CreatedEntities, restored.CreatedEntities);
        Assert.Equal("managed_dotnet", restored.RuntimeAndPolicyPins.RuntimeId);
        Assert.Equal(
            "sha256:eff5ba7a65f9eb9d58cd6a05895050caaa4cde6c285bd9fa9662370ef896474b",
            restored.CheckpointDigest);

        var injected = JsonNode.Parse(checkpoint.Serialize())!.AsObject();
        injected["arbitrary_handles"] = new JsonArray("DEAD");
        Assert.Equal(
            "rollback_conflict",
            Assert.Throws<ProtocolValidationException>(
                () => CadRollbackCheckpointV1.Parse(injected.ToJsonString())).Code);
    }

    [Fact]
    public void Parser_UsesIdsAndDigestsAndRejectsRawHandles()
    {
        using var valid = JsonDocument.Parse(
            $$"""
            {
              "checkpoint_id":"{{Checkpoint().CheckpointId}}",
              "checkpoint_digest":"{{Checkpoint().CheckpointDigest}}",
              "rollback_plan_id":"plan-1",
              "rollback_execution_digest":"{{Digest('8')}}",
              "expires_at":"2999-01-01T00:00:00+00:00"
            }
            """);
        var parsed = Phase7RollbackParser.Parse(
            "cad.rollback.preview",
            valid.RootElement);
        Assert.Equal("plan-1", parsed.RollbackPlanId);

        var injected = JsonNode.Parse(valid.RootElement.GetRawText())!.AsObject();
        injected["entity_handles"] = new JsonArray("1A");
        using var unsafePayload = JsonDocument.Parse(injected.ToJsonString());
        var error = Assert.Throws<ProtocolValidationException>(
            () => Phase7RollbackParser.Parse(
                "cad.rollback.preview",
                unsafePayload.RootElement));
        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void CommitAndRollback_AreAtomicStrictAndIdempotent()
    {
        var checkpoint = Checkpoint();
        var model = new Phase7RollbackTransactionModel();
        Assert.Throws<InvalidOperationException>(
            () => model.Commit(checkpoint, failBeforeCommit: true));
        Assert.Empty(model.Entities);
        Assert.Empty(model.Checkpoints);

        model.Commit(checkpoint);
        var receipt = RollbackReceipt(checkpoint);
        Assert.Throws<InvalidOperationException>(
            () => model.Rollback(receipt, failBeforeCommit: true));
        Assert.Single(model.Entities);
        Assert.Empty(model.Receipts);

        var committed = model.Rollback(receipt);
        Assert.Empty(model.Entities);
        Assert.Same(committed, model.Rollback(receipt));
        Assert.Single(model.Receipts);

        var conflict = RollbackReceipt(
            checkpoint,
            planDigest: Digest('9'));
        var error = Assert.Throws<ProtocolValidationException>(
            () => model.Rollback(conflict));
        Assert.Equal("duplicate_payload_mismatch", error.Code);
    }

    [Fact]
    public void DriftAndMissingCheckpoint_FailClosed()
    {
        var checkpoint = Checkpoint();
        var model = new Phase7RollbackTransactionModel();
        model.Commit(checkpoint);
        model.SimulateEntityDrift("1A", Digest('9'));

        var drift = Assert.Throws<ProtocolValidationException>(
            () => model.Rollback(RollbackReceipt(checkpoint)));
        Assert.Equal("rollback_conflict", drift.Code);

        var oldReceiptModel = new Phase7RollbackTransactionModel();
        var missing = Assert.Throws<ProtocolValidationException>(
            () => oldReceiptModel.Rollback(RollbackReceipt(checkpoint)));
        Assert.Equal("rollback_conflict", missing.Code);
    }

    private static CadRollbackCheckpointV1 Checkpoint()
    {
        var receipt = new DurableProgramReceiptV02(
            "preview-1",
            Digest('1'),
            Digest('2'),
            "document-1",
            "41",
            "42",
            [new DurableEntityEvidence(
                "1A",
                "LINE",
                "CAD-MCP",
                new CadBounds(0, 0, 0, 10, 0, 0))],
            ["CAD-MCP"]);
        var program = new CadProgramV02(
            "program-1",
            1,
            "device-1",
            "snapshot-1",
            "document-1",
            "41",
            [],
            [],
            CadProgramBudgets.Defaults,
            Digest('1'));
        return CadRollbackCheckpointV1.Create(
            receipt,
            program,
            new CadPreviewReference("preview-1", Digest('3')),
            Binding(),
            [new CadCheckpointEntity("1A", "LINE", "CAD-MCP", Digest('4'))],
            nonEntityObjectCreated: true,
            new DateTimeOffset(2026, 7, 28, 0, 0, 0, TimeSpan.Zero));
    }

    private static DurableRollbackReceiptV1 RollbackReceipt(
        CadRollbackCheckpointV1 checkpoint,
        string? planDigest = null) =>
        DurableRollbackReceiptV1.Create(
            "rollback-receipt-1",
            checkpoint,
            "rollback-plan-1",
            planDigest ?? Digest('5'),
            Digest('6'),
            "42",
            "43",
            [new CadRemovedEntityEvidence("1A", "LINE", Digest('4'))],
            new DateTimeOffset(2026, 7, 28, 0, 1, 0, TimeSpan.Zero));

    private static CadExecutionBinding Binding() => new(
        Digest('1'),
        Digest('2'),
        "document-1",
        "41",
        "managed_dotnet",
        "primary",
        "R25",
        "0.2.0",
        "autocad.managed_host.r25",
        "0.2.0",
        Digest('7'),
        Digest('8'),
        CadProgramV02Contract.RegistryVersion,
        CadProgramV02Contract.RegistryDigest,
        CadProgramV02Contract.PolicyVersion);

    private static string Digest(char value) => $"sha256:{new string(value, 64)}";
}

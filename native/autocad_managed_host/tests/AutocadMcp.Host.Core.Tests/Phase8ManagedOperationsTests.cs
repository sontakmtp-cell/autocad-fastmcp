using System.Text.Json.Nodes;
using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class Phase8ManagedOperationsTests
{
    [Fact]
    public void Registry_IsExplicitAndDoesNotContainDestructiveOrTopologyOperations()
    {
        var kinds = Phase8ManagedOperationRegistry.Operations
            .Select(operation => operation.Kind)
            .ToHashSet(StringComparer.Ordinal);

        Assert.Equal(
            new HashSet<string>(StringComparer.Ordinal)
            {
                "copy_entity",
                "pattern_linear",
                "pattern_rectangular",
                "pattern_polar",
                "offset_entity",
                "move_entity",
                "rotate_entity",
                "scale_entity"
            },
            kinds);
        Assert.DoesNotContain("delete_entity", kinds);
        Assert.DoesNotContain("trim_entity", kinds);
        Assert.DoesNotContain("fillet_entity", kinds);
        Assert.DoesNotContain("chamfer_entity", kinds);
    }

    [Fact]
    public void Batch_RequiresStableUniqueOutputMapping()
    {
        var target = Target("LINE");
        var operations = new CadManagedOperation[]
        {
            new CadCopyEntityOperation(
                "copy-1",
                target,
                new CadManagedVector(10, 0, 0),
                "copy-1.out"),
            new CadLinearPatternOperation(
                "pattern-1",
                target,
                new CadManagedVector(0, 5, 0),
                3,
                ["pattern-1.out-1", "pattern-1.out-2"])
        };

        Phase8ManagedOperationRegistry.ValidateBatch(operations);

        var duplicate = operations.Append(
            new CadCopyEntityOperation(
                "copy-2",
                target,
                new CadManagedVector(1, 0, 0),
                "copy-1.out")).ToArray();
        var error = Assert.Throws<ProtocolValidationException>(
            () => Phase8ManagedOperationRegistry.ValidateBatch(duplicate));
        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void UnsupportedAndCustomObjects_FailCapabilityClosed()
    {
        var operation = new CadMoveEntityOperation(
            "move-1",
            Target("AECC_PIPE"),
            new CadManagedVector(1, 0, 0));

        var error = Assert.Throws<ProtocolValidationException>(
            () => Phase8ManagedOperationRegistry.ValidateBatch([operation]));

        Assert.Equal("capability_missing", error.Code);
    }

    [Fact]
    public void SealedPlan_RecomputesTargetsAndOperationPayload()
    {
        var operation = new CadCopyEntityOperation(
            "copy-1",
            Target("LINE"),
            new CadManagedVector(10, 0, 0),
            "copy-1.out");
        var plan = Plan([operation]);

        plan.Validate();
        var identity = CadManagedEffectIdentity.Create(plan);
        Assert.Equal(identity, CadManagedEffectIdentity.Create(plan));

        var changed = plan with
        {
            Operations =
            [
                operation with
                {
                    Displacement = new CadManagedVector(11, 0, 0)
                }
            ]
        };
        var error = Assert.Throws<ProtocolValidationException>(changed.Validate);
        Assert.Equal("plan_mismatch", error.Code);
    }

    [Fact]
    public void HostAdmission_UsesIndependentPinsAndDefaultClosedTransformFlags()
    {
        var move = new CadMoveEntityOperation(
            "move-1",
            Target("LINE"),
            new CadManagedVector(1, 0, 0));
        var plan = Plan([move]);
        var descriptor = Phase8ManagedOperationRegistry.Require("move_entity", "LINE");
        var admission = new CadManagedHostAdmission(
            Pins(),
            new HashSet<string>(StringComparer.Ordinal)
            {
                descriptor.CapabilityKey
            },
            new HashSet<string>(StringComparer.Ordinal)
            {
                "move_entity"
            },
            IndependentCapabilityEvidenceVerified: true,
            CreatePackEnabled: true,
            CheckpointV2Enabled: false,
            TransformPackEnabled: false);

        var disabled = Assert.Throws<ProtocolValidationException>(
            () => admission.AssertAllowed(plan));
        Assert.Equal("capability_missing", disabled.Code);

        var stalePins = admission with
        {
            ActualPins = Pins() with { RolloutPolicyDigest = Digest('c') },
            CheckpointV2Enabled = true,
            TransformPackEnabled = true
        };
        var stale = Assert.Throws<ProtocolValidationException>(
            () => stalePins.AssertAllowed(plan));
        Assert.Equal("runtime_changed", stale.Code);
    }

    [Fact]
    public void HostAdmission_RejectsMixedCheckpointStrategies()
    {
        var copy = new CadCopyEntityOperation(
            "copy-1",
            Target("LINE"),
            new CadManagedVector(10, 0, 0),
            "copy-1.out");
        var move = new CadMoveEntityOperation(
            "move-1",
            Target("LINE"),
            new CadManagedVector(0, 10, 0));
        var plan = Plan([copy, move]);
        var capabilities = plan.Operations
            .Select(operation => Phase8ManagedOperationRegistry.Require(
                operation.Kind,
                operation.Target.EntityType).CapabilityKey)
            .ToHashSet(StringComparer.Ordinal);
        var admission = new CadManagedHostAdmission(
            Pins(),
            capabilities,
            new HashSet<string>(StringComparer.Ordinal)
            {
                "copy_entity",
                "move_entity"
            },
            IndependentCapabilityEvidenceVerified: true,
            CreatePackEnabled: true,
            CheckpointV2Enabled: true,
            TransformPackEnabled: true);

        var error = Assert.Throws<ProtocolValidationException>(
            () => admission.AssertAllowed(plan));

        Assert.Equal("capability_missing", error.Code);
        Assert.Contains("compound atomic rollback", error.Message);
    }

    [Fact]
    public void CheckpointV2_RoundTripsBoundedHostRestoreDescriptors()
    {
        var checkpoint = Checkpoint();

        var restored = CadRollbackCheckpointV2.Parse(checkpoint.Serialize());

        Assert.Equal(
            Phase8ManagedOperationContract.CheckpointV2Version,
            restored.SchemaVersion);
        Assert.Equal(checkpoint.CheckpointDigest, restored.CheckpointDigest);
        Assert.Equal(checkpoint.Serialize(), restored.Serialize());
        Assert.InRange(
            restored.RestoreBudgetBytes,
            1,
            Phase8ManagedOperationContract.MaxRestoreBytes);

        var injected = JsonNode.Parse(checkpoint.Serialize())!.AsObject();
        injected["raw_handle"] = "DEAD";
        var error = Assert.Throws<ProtocolValidationException>(
            () => CadRollbackCheckpointV2.Parse(injected.ToJsonString()));
        Assert.Equal("rollback_conflict", error.Code);

        var nested = JsonNode.Parse(checkpoint.Serialize())!.AsObject();
        nested["restore_entries"]![0]!["restore_descriptor"]!["raw_dxf"] = "0A";
        var nestedError = Assert.Throws<ProtocolValidationException>(
            () => CadRollbackCheckpointV2.Parse(nested.ToJsonString()));
        Assert.Equal("rollback_conflict", nestedError.Code);
    }

    [Fact]
    public void CheckpointV2_StrictEntityUnionRejectsMixedRestoreGeometry()
    {
        var descriptor = new CadEntityRestoreDescriptorV2(
            "cad.restore-descriptor/2",
            "restore_allowlisted_preimage",
            "LINE",
            new CadEntityStyleV2("0", "ByLayer", 1, -1, true, 256),
            new CadManagedPoint(0, 0, 0),
            new CadManagedPoint(10, 0, 0),
            new CadManagedPoint(5, 5, 0),
            null,
            2,
            null,
            null,
            null);

        var error = Assert.Throws<ProtocolValidationException>(descriptor.Validate);

        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void ModifiedReceipt_CannotMaterializeWithoutCheckpointV2()
    {
        var receipt = new CadManagedOperationReceipt(
            "receipt-1",
            Digest('1'),
            Digest('2'),
            Digest('8'),
            Digest('9'),
            "document-1",
            "10",
            "11",
            [],
            [new CadManagedModifiedEntity(
                "move-1",
                "entity-1",
                "LINE",
                Digest('3'),
                Digest('4'))],
            null,
            null,
            null,
            null,
            Pins());

        var error = Assert.Throws<ProtocolValidationException>(receipt.Validate);

        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void CreateEquivalentReceipt_RequiresCheckpointV1Ownership()
    {
        var receipt = new CadManagedOperationReceipt(
            "receipt-1",
            Digest('1'),
            Digest('2'),
            Digest('8'),
            Digest('9'),
            "document-1",
            "10",
            "11",
            [new CadManagedOutputMapping(
                "copy-1",
                "copy-1.out",
                "entity-2",
                "LINE",
                "0",
                Digest('4'))],
            [],
            null,
            null,
            null,
            null,
            Pins());

        var error = Assert.Throws<ProtocolValidationException>(receipt.Validate);

        Assert.Equal("program_invalid", error.Code);
    }

    [Fact]
    public void TransformCommitAndRestore_AreAtomicAndDuplicateSafe()
    {
        var checkpoint = Checkpoint();
        var receipt = new CadManagedOperationReceipt(
            "receipt-1",
            checkpoint.PlanDigest,
            checkpoint.EffectDigest,
            Digest('8'),
            checkpoint.TargetSetDigest,
            checkpoint.DocumentId,
            checkpoint.DocumentRevisionBefore,
            checkpoint.DocumentRevisionAfter,
            [],
            [new CadManagedModifiedEntity(
                "move-1",
                "entity-1",
                "LINE",
                Digest('3'),
                Digest('4'))],
            null,
            null,
            checkpoint.CheckpointId,
            checkpoint.CheckpointDigest,
            checkpoint.RuntimePins);
        var record = CadManagedCommitRecord.Create(
            receipt,
            new DateTimeOffset(2026, 7, 28, 0, 0, 0, TimeSpan.Zero));
        var model = new Phase8TransactionModel();
        model.Seed("entity-1", Digest('3'));

        Assert.Throws<InvalidOperationException>(
            () => model.Commit(record, null, checkpoint, failBeforeCommit: true));
        Assert.Equal(Digest('3'), model.Entities["entity-1"]);
        Assert.Empty(model.Commits);

        Assert.Same(record, model.Commit(record, null, checkpoint));
        Assert.Same(record, model.Commit(record, null, checkpoint));
        Assert.Equal(Digest('4'), model.Entities["entity-1"]);
        var mismatchRecord = CadManagedCommitRecord.Create(
            receipt with { EffectIdentityDigest = Digest('f') },
            new DateTimeOffset(2026, 7, 28, 0, 0, 1, TimeSpan.Zero));
        var mismatch = Assert.Throws<ProtocolValidationException>(
            () => model.Commit(mismatchRecord, null, checkpoint));
        Assert.Equal("duplicate_payload_mismatch", mismatch.Code);

        var restore = CadManagedRestoreReceipt.Create(
            checkpoint,
            "12",
            [new CadManagedModifiedEntity(
                "move-1",
                "entity-1",
                "LINE",
                Digest('4'),
                Digest('3'))],
            new DateTimeOffset(2026, 7, 28, 0, 1, 0, TimeSpan.Zero));
        Assert.Throws<InvalidOperationException>(
            () => model.Restore(checkpoint, restore, failBeforeCommit: true));
        Assert.Equal(Digest('4'), model.Entities["entity-1"]);
        Assert.Empty(model.RestoreReceipts);

        Assert.Same(restore, model.Restore(checkpoint, restore));
        Assert.Same(restore, model.Restore(checkpoint, restore));
        Assert.Equal(Digest('3'), model.Entities["entity-1"]);
    }

    private static CadRollbackCheckpointV2 Checkpoint()
    {
        var descriptor = new CadEntityRestoreDescriptorV2(
            "cad.restore-descriptor/2",
            "restore_allowlisted_preimage",
            "LINE",
            new CadEntityStyleV2("0", "ByLayer", 1, -1, true, 256),
            new CadManagedPoint(0, 0, 0),
            new CadManagedPoint(10, 0, 0),
            null,
            null,
            null,
            null,
            null,
            null);
        var dependencies = new[]
        {
            new CadDependencyRefV2("layer", "0", Digest('a')),
            new CadDependencyRefV2("linetype", "ByLayer", Digest('b'))
        };
        return CadRollbackCheckpointV2.Create(
            "receipt-1",
            Digest('1'),
            Digest('2'),
            "document-1",
            "10",
            "11",
            [new CadRestoreEntryV2(
                "move-1",
                Target("LINE"),
                Digest('4'),
                "MODEL_SPACE",
                "snapshot-1",
                dependencies,
                CadRestoreEvidenceDigest.Dependencies(dependencies),
                descriptor,
                CadRestoreEvidenceDigest.Descriptor(descriptor))],
            Pins(),
            "owner-1",
            "device-1",
            "snapshot-1",
            Digest('c'),
            new DateTimeOffset(2026, 7, 28, 0, 0, 0, TimeSpan.Zero));
    }

    private static CadStableEntityRef Target(string type) => new(
        "document-1",
        "entity-1",
        type,
        Digest('3'));

    private static CadManagedRuntimePins Pins() => new(
        "managed_dotnet",
        "R25",
        "0.2.0",
        Digest('5'),
        Phase8ManagedOperationContract.RegistryVersion,
        Phase8ManagedOperationRegistry.RegistryDigest,
        Digest('7'),
        Digest('8'),
        Digest('9'),
        Digest('a'),
        Digest('b'));

    private static CadManagedSealedPlan Plan(
        IReadOnlyList<CadManagedOperation> operations) => new(
        Phase8ManagedAdmissionContract.SealedPlanVersion,
        Digest('1'),
        Digest('2'),
        CadManagedSealedPlan.ComputeTargetSetDigest(operations),
        CadManagedSealedPlan.ComputeOperationPayloadDigest(operations),
        Digest('3'),
        Digest('4'),
        "owner-1",
        "device-1",
        "document-1",
        "snapshot-1",
        "10",
        Pins(),
        operations);

    private static string Digest(char value) => $"sha256:{new string(value, 64)}";
}

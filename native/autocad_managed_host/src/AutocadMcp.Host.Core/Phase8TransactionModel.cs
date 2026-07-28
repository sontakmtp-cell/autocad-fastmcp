namespace AutocadMcp.Host.Core;

public sealed class Phase8TransactionModel
{
    private readonly Dictionary<string, string> _entities = new(StringComparer.Ordinal);
    private readonly Dictionary<string, CadManagedCommitRecord> _commits =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, CadCreatedOutputCheckpointV1> _createdCheckpoints =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, CadRollbackCheckpointV2> _restoreCheckpoints =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, CadManagedRestoreReceipt> _restoreReceipts =
        new(StringComparer.Ordinal);
    private readonly Dictionary<string, CadCreatedOutputRollbackReceiptV1>
        _createdRollbackReceipts = new(StringComparer.Ordinal);

    public IReadOnlyDictionary<string, string> Entities => _entities;
    public IReadOnlyDictionary<string, CadManagedCommitRecord> Commits => _commits;
    public IReadOnlyDictionary<string, CadRollbackCheckpointV2> RestoreCheckpoints =>
        _restoreCheckpoints;
    public IReadOnlyDictionary<string, CadManagedRestoreReceipt> RestoreReceipts =>
        _restoreReceipts;

    public void Seed(string entityId, string fingerprint) =>
        _entities.Add(entityId, fingerprint);

    public CadManagedCommitRecord Commit(
        CadManagedCommitRecord record,
        CadCreatedOutputCheckpointV1? createdCheckpoint,
        CadRollbackCheckpointV2? restoreCheckpoint,
        bool failBeforeCommit = false)
    {
        var receipt = record.Receipt;
        if (_commits.TryGetValue(receipt.ReceiptId, out var existing))
        {
            if (existing.ReceiptDigest != record.ReceiptDigest)
            {
                throw Mismatch();
            }
            return existing;
        }
        if ((receipt.CreatedOutputs.Count != 0) != (createdCheckpoint is not null) ||
            (receipt.ModifiedEntities.Count != 0) != (restoreCheckpoint is not null) ||
            receipt.CheckpointV1Digest != createdCheckpoint?.CheckpointDigest ||
            receipt.CheckpointV2Digest != restoreCheckpoint?.CheckpointDigest)
        {
            throw Invalid("Receipt and checkpoint material do not match.");
        }

        var entities = new Dictionary<string, string>(_entities, StringComparer.Ordinal);
        foreach (var output in receipt.CreatedOutputs)
        {
            if (!entities.TryAdd(output.EntityId, output.Fingerprint))
            {
                throw Invalid("Created output already exists.");
            }
        }
        foreach (var modified in receipt.ModifiedEntities)
        {
            if (!entities.TryGetValue(modified.EntityId, out var current) ||
                current != modified.FingerprintBefore)
            {
                throw Invalid("Transform target drifted before commit.");
            }
            entities[modified.EntityId] = modified.FingerprintAfter;
        }

        var commits = new Dictionary<string, CadManagedCommitRecord>(
            _commits,
            StringComparer.Ordinal)
        {
            [receipt.ReceiptId] = record
        };
        var created = new Dictionary<string, CadCreatedOutputCheckpointV1>(
            _createdCheckpoints,
            StringComparer.Ordinal);
        if (createdCheckpoint is not null)
        {
            created.Add(createdCheckpoint.CheckpointId, createdCheckpoint);
        }
        var restore = new Dictionary<string, CadRollbackCheckpointV2>(
            _restoreCheckpoints,
            StringComparer.Ordinal);
        if (restoreCheckpoint is not null)
        {
            restore.Add(restoreCheckpoint.CheckpointId, restoreCheckpoint);
        }
        if (failBeforeCommit)
        {
            throw new InvalidOperationException("Injected Phase 8 commit failure.");
        }
        Replace(_entities, entities);
        Replace(_commits, commits);
        Replace(_createdCheckpoints, created);
        Replace(_restoreCheckpoints, restore);
        return record;
    }

    public CadManagedRestoreReceipt Restore(
        CadRollbackCheckpointV2 checkpoint,
        CadManagedRestoreReceipt receipt,
        bool failBeforeCommit = false)
    {
        if (_restoreReceipts.TryGetValue(receipt.RestoreReceiptId, out var existing))
        {
            if (existing.ReceiptDigest != receipt.ReceiptDigest)
            {
                throw Mismatch();
            }
            return existing;
        }
        if (!_restoreCheckpoints.TryGetValue(checkpoint.CheckpointId, out var durable) ||
            durable.CheckpointDigest != checkpoint.CheckpointDigest)
        {
            throw Invalid("Checkpoint v2 is unavailable.");
        }
        var entities = new Dictionary<string, string>(_entities, StringComparer.Ordinal);
        foreach (var entry in checkpoint.RestoreEntries)
        {
            if (!entities.TryGetValue(entry.TargetBefore.EntityId, out var current) ||
                current != entry.FingerprintAfter)
            {
                throw Invalid("Transform target drifted before restore.");
            }
            entities[entry.TargetBefore.EntityId] = entry.TargetBefore.Fingerprint;
        }
        var receipts = new Dictionary<string, CadManagedRestoreReceipt>(
            _restoreReceipts,
            StringComparer.Ordinal)
        {
            [receipt.RestoreReceiptId] = receipt
        };
        if (failBeforeCommit)
        {
            throw new InvalidOperationException("Injected Phase 8 restore failure.");
        }
        Replace(_entities, entities);
        Replace(_restoreReceipts, receipts);
        return receipt;
    }

    public CadCreatedOutputRollbackReceiptV1 RollbackCreated(
        CadCreatedOutputCheckpointV1 checkpoint,
        CadCreatedOutputRollbackReceiptV1 receipt,
        bool failBeforeCommit = false)
    {
        if (_createdRollbackReceipts.TryGetValue(
                receipt.RollbackReceiptId,
                out var existing))
        {
            if (existing.ReceiptDigest != receipt.ReceiptDigest)
            {
                throw Mismatch();
            }
            return existing;
        }
        if (!_createdCheckpoints.TryGetValue(checkpoint.CheckpointId, out var durable) ||
            durable.CheckpointDigest != checkpoint.CheckpointDigest)
        {
            throw Invalid("Created-output checkpoint is unavailable.");
        }
        var entities = new Dictionary<string, string>(_entities, StringComparer.Ordinal);
        foreach (var output in checkpoint.CreatedOutputs)
        {
            if (!entities.TryGetValue(output.EntityId, out var current) ||
                current != output.Fingerprint)
            {
                throw Invalid("Created output drifted before rollback.");
            }
            entities.Remove(output.EntityId);
        }
        var receipts = new Dictionary<string, CadCreatedOutputRollbackReceiptV1>(
            _createdRollbackReceipts,
            StringComparer.Ordinal)
        {
            [receipt.RollbackReceiptId] = receipt
        };
        if (failBeforeCommit)
        {
            throw new InvalidOperationException(
                "Injected Phase 8 created-output rollback failure.");
        }
        Replace(_entities, entities);
        Replace(_createdRollbackReceipts, receipts);
        return receipt;
    }

    private static void Replace<T>(
        Dictionary<string, T> target,
        Dictionary<string, T> source)
    {
        target.Clear();
        foreach (var item in source)
        {
            target.Add(item.Key, item.Value);
        }
    }

    private static ProtocolValidationException Invalid(string message) =>
        new("rollback_conflict", message);

    private static ProtocolValidationException Mismatch() =>
        new(
            "duplicate_payload_mismatch",
            "Durable Phase 8 identity was reused with another payload.");
}

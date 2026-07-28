using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.DatabaseServices;

namespace AutocadMcp.Host.R25;

/// <summary>
/// Stores the succeeded receipt in the drawing's Named Objects Dictionary.
/// The receipt and created entities commit in the same database transaction.
/// </summary>
internal static class DrawingProgramLedger
{
    private const string LedgerDictionaryKey = "AUTOCAD_MCP_PROGRAM_RECEIPTS";
    private const string CheckpointDictionaryKey = "AUTOCAD_MCP_ROLLBACK_CHECKPOINTS";
    private const string RollbackReceiptDictionaryKey = "AUTOCAD_MCP_ROLLBACK_RECEIPTS";
    private const int MaximumReceipts = 4096;

    public static DurableProgramReceipt? Find(
        Database database,
        Transaction transaction,
        string idempotencyKey)
    {
        try
        {
            return FindCore(database, transaction, idempotencyKey);
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception exception)
        {
            throw new ProtocolValidationException(
                "ledger_read_failed",
                $"Drawing ledger read failed with AutoCAD status {exception.ErrorStatus}.");
        }
    }

    public static DurableProgramReceiptV02? FindV02(
        Database database,
        Transaction transaction,
        string idempotencyKey)
    {
        var lookup = new DurableProgramReceiptV02(
            idempotencyKey,
            $"sha256:{new string('0', 64)}",
            $"sha256:{new string('0', 64)}",
            "lookup",
            "1",
            "1",
            [],
            []);
        return ReadV02(database, transaction, lookup.ReceiptId);
    }

    public static DurableProgramReceiptV02? FindByReceiptIdV02(
        Database database,
        Transaction transaction,
        string receiptId)
    {
        if (receiptId.Length != 52 ||
            !receiptId.StartsWith("AUTOCAD_MCP_PROGRAM_", StringComparison.Ordinal) ||
            receiptId[20..].Any(character => !Uri.IsHexDigit(character) || char.IsUpper(character)))
        {
            throw new ProtocolValidationException(
                "program_invalid",
                "Receipt identifier is malformed.");
        }
        return ReadV02(database, transaction, receiptId);
    }

    private static DurableProgramReceiptV02? ReadV02(
        Database database,
        Transaction transaction,
        string receiptId)
    {
        try
        {
            var dictionary = GetLedgerDictionary(database, transaction, create: false);
            if (dictionary is null || !dictionary.Contains(receiptId))
            {
                return null;
            }
            var record = (Xrecord)transaction.GetObject(
                dictionary.GetAt(receiptId),
                OpenMode.ForRead);
            var values = record.Data?.AsArray();
            if (values is null ||
                values.Length is < 1 or > 64 ||
                values.Any(value =>
                    value.TypeCode != (int)DxfCode.Text ||
                    value.Value is not string))
            {
                throw new ProtocolValidationException(
                    "ledger_corrupt",
                    "Drawing contains an invalid CAD Program receipt.");
            }
            var json = string.Concat(values.Select(value => (string)value.Value));
            var receipt = DurableProgramReceiptV02.Parse(json);
            if (receipt.ReceiptId != receiptId)
            {
                throw new ProtocolValidationException(
                    "ledger_corrupt",
                    "Drawing CAD Program receipt key does not match its content.");
            }
            return receipt;
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception exception)
        {
            throw new ProtocolValidationException(
                "ledger_read_failed",
                $"Drawing ledger read failed with AutoCAD status {exception.ErrorStatus}.");
        }
    }

    private static DurableProgramReceipt? FindCore(
        Database database,
        Transaction transaction,
        string idempotencyKey)
    {
        var lookup = new DurableProgramReceipt(
            idempotencyKey,
            $"sha256:{new string('0', 64)}",
            $"sha256:{new string('0', 64)}",
            "checkpoint-lookup");
        var dictionary = GetLedgerDictionary(database, transaction, create: false);
        if (dictionary is null)
        {
            return null;
        }
        if (!dictionary.Contains(lookup.DictionaryKey))
        {
            return null;
        }

        var record = (Xrecord)transaction.GetObject(
            dictionary.GetAt(lookup.DictionaryKey),
            OpenMode.ForRead);
        var values = record.Data?.AsArray();
        if (values is not [{ TypeCode: (int)DxfCode.Text, Value: string json }])
        {
            throw new ProtocolValidationException(
                "ledger_corrupt",
                "Drawing contains an invalid CAD Program receipt.");
        }
        var receipt = DurableProgramReceipt.Parse(json);
        if (receipt.IdempotencyKey != idempotencyKey ||
            receipt.DictionaryKey != lookup.DictionaryKey)
        {
            throw new ProtocolValidationException(
                "ledger_corrupt",
                "Drawing CAD Program receipt key does not match its content.");
        }
        return receipt;
    }

    public static void Add(
        Database database,
        Transaction transaction,
        DurableProgramReceipt receipt)
    {
        try
        {
            AddCore(database, transaction, receipt);
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception exception)
        {
            throw new ProtocolValidationException(
                "ledger_write_failed",
                $"Drawing ledger write failed with AutoCAD status {exception.ErrorStatus}.");
        }
    }

    public static void AddV02(
        Database database,
        Transaction transaction,
        DurableProgramReceiptV02 receipt)
    {
        try
        {
            var dictionary = GetLedgerDictionary(database, transaction, create: true)
                ?? throw new InvalidOperationException("CAD Program ledger was not created.");
            if (dictionary.Contains(receipt.ReceiptId))
            {
                throw new ProtocolValidationException(
                    "duplicate_payload_mismatch",
                    "CAD Program receipt already exists.");
            }
            if (dictionary.Count >= MaximumReceipts)
            {
                throw new ProtocolValidationException(
                    "ledger_full",
                    "Drawing CAD Program receipt ledger reached its bounded capacity.");
            }
            dictionary.UpgradeOpen();
            var serialized = receipt.Serialize();
            var chunks = Enumerable.Range(0, (serialized.Length + 1999) / 2000)
                .Select(index => new TypedValue(
                    (int)DxfCode.Text,
                    serialized.Substring(
                        index * 2000,
                        Math.Min(2000, serialized.Length - (index * 2000)))))
                .ToArray();
            var record = new Xrecord { Data = new ResultBuffer(chunks) };
            dictionary.SetAt(receipt.ReceiptId, record);
            transaction.AddNewlyCreatedDBObject(record, true);
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception exception)
        {
            throw new ProtocolValidationException(
                "ledger_write_failed",
                $"Drawing ledger write failed with AutoCAD status {exception.ErrorStatus}.");
        }
    }

    public static CadRollbackCheckpointV1? FindCheckpoint(
        Database database,
        Transaction transaction,
        string checkpointId)
    {
        CadRollbackCheckpointV1.RequireId(checkpointId, 64);
        var json = ReadRecord(
            database,
            transaction,
            CheckpointDictionaryKey,
            checkpointId);
        if (json is null)
        {
            return null;
        }
        var checkpoint = CadRollbackCheckpointV1.Parse(json);
        if (checkpoint.CheckpointId != checkpointId)
        {
            throw new ProtocolValidationException(
                "ledger_corrupt",
                "Checkpoint key does not match its durable content.");
        }
        return checkpoint;
    }

    public static void AddCheckpoint(
        Database database,
        Transaction transaction,
        CadRollbackCheckpointV1 checkpoint) =>
        AddRecord(
            database,
            transaction,
            CheckpointDictionaryKey,
            checkpoint.CheckpointId,
            checkpoint.Serialize());

    public static DurableRollbackReceiptV1? FindRollbackReceipt(
        Database database,
        Transaction transaction,
        string rollbackReceiptId)
    {
        CadRollbackCheckpointV1.RequireId(rollbackReceiptId, 128);
        var lookup = new DurableRollbackReceiptV1(
            rollbackReceiptId,
            "lookup",
            $"sha256:{new string('0', 64)}",
            "lookup",
            $"sha256:{new string('0', 64)}",
            "lookup",
            $"sha256:{new string('0', 64)}",
            $"sha256:{new string('0', 64)}",
            "lookup",
            "1",
            "1",
            [],
            new CadExecutionBinding(
                $"sha256:{new string('0', 64)}",
                $"sha256:{new string('0', 64)}",
                "lookup",
                "1",
                "managed_dotnet",
                "primary",
                "R25",
                "lookup",
                "lookup",
                "lookup",
                $"sha256:{new string('0', 64)}",
                $"sha256:{new string('0', 64)}",
                "lookup",
                $"sha256:{new string('0', 64)}",
                "lookup"),
            DateTimeOffset.UnixEpoch.ToString("O"),
            $"sha256:{new string('0', 64)}");
        var json = ReadRecord(
            database,
            transaction,
            RollbackReceiptDictionaryKey,
            lookup.DictionaryKey);
        if (json is null)
        {
            return null;
        }
        var receipt = DurableRollbackReceiptV1.Parse(json);
        if (receipt.RollbackReceiptId != rollbackReceiptId ||
            receipt.DictionaryKey != lookup.DictionaryKey)
        {
            throw new ProtocolValidationException(
                "ledger_corrupt",
                "Rollback receipt key does not match its durable content.");
        }
        return receipt;
    }

    public static void AddRollbackReceipt(
        Database database,
        Transaction transaction,
        DurableRollbackReceiptV1 receipt) =>
        AddRecord(
            database,
            transaction,
            RollbackReceiptDictionaryKey,
            receipt.DictionaryKey,
            receipt.Serialize());

    private static string? ReadRecord(
        Database database,
        Transaction transaction,
        string dictionaryKey,
        string recordKey)
    {
        try
        {
            var dictionary = GetNamedDictionary(
                database,
                transaction,
                dictionaryKey,
                create: false);
            if (dictionary is null || !dictionary.Contains(recordKey))
            {
                return null;
            }
            var record = (Xrecord)transaction.GetObject(
                dictionary.GetAt(recordKey),
                OpenMode.ForRead);
            var values = record.Data?.AsArray();
            if (values is null ||
                values.Length is < 1 or > 64 ||
                values.Any(value =>
                    value.TypeCode != (int)DxfCode.Text ||
                    value.Value is not string))
            {
                throw new ProtocolValidationException(
                    "ledger_corrupt",
                    "Drawing contains a malformed Phase 7 ledger record.");
            }
            return string.Concat(values.Select(value => (string)value.Value));
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception exception)
        {
            throw new ProtocolValidationException(
                "ledger_read_failed",
                $"Drawing ledger read failed with AutoCAD status {exception.ErrorStatus}.");
        }
    }

    private static void AddRecord(
        Database database,
        Transaction transaction,
        string dictionaryKey,
        string recordKey,
        string serialized)
    {
        try
        {
            var dictionary = GetNamedDictionary(
                database,
                transaction,
                dictionaryKey,
                create: true)
                ?? throw new InvalidOperationException("Phase 7 ledger was not created.");
            if (dictionary.Contains(recordKey))
            {
                throw new ProtocolValidationException(
                    "duplicate_payload_mismatch",
                    "Phase 7 durable key already exists.");
            }
            if (dictionary.Count >= MaximumReceipts)
            {
                throw new ProtocolValidationException(
                    "ledger_full",
                    "Phase 7 drawing ledger reached its bounded capacity.");
            }
            dictionary.UpgradeOpen();
            var chunks = Enumerable.Range(0, (serialized.Length + 1999) / 2000)
                .Select(index => new TypedValue(
                    (int)DxfCode.Text,
                    serialized.Substring(
                        index * 2000,
                        Math.Min(2000, serialized.Length - (index * 2000)))))
                .ToArray();
            var record = new Xrecord { Data = new ResultBuffer(chunks) };
            dictionary.SetAt(recordKey, record);
            transaction.AddNewlyCreatedDBObject(record, true);
        }
        catch (ProtocolValidationException)
        {
            throw;
        }
        catch (Autodesk.AutoCAD.Runtime.Exception exception)
        {
            throw new ProtocolValidationException(
                "ledger_write_failed",
                $"Drawing ledger write failed with AutoCAD status {exception.ErrorStatus}.");
        }
    }

    private static void AddCore(
        Database database,
        Transaction transaction,
        DurableProgramReceipt receipt)
    {
        var dictionary = GetLedgerDictionary(database, transaction, create: true)
            ?? throw new InvalidOperationException("CAD Program ledger was not created.");
        if (dictionary.Contains(receipt.DictionaryKey))
        {
            throw new ProtocolValidationException(
                "duplicate_payload_mismatch",
                "CAD Program receipt already exists.");
        }
        if (dictionary.Count >= MaximumReceipts)
        {
            throw new ProtocolValidationException(
                "ledger_full",
                "Drawing CAD Program receipt ledger reached its bounded capacity.");
        }
        dictionary.UpgradeOpen();
        var record = new Xrecord
        {
            Data = new ResultBuffer(
                new TypedValue((int)DxfCode.Text, receipt.Serialize()))
        };
        dictionary.SetAt(receipt.DictionaryKey, record);
        transaction.AddNewlyCreatedDBObject(record, true);
    }

    private static DBDictionary? GetLedgerDictionary(
        Database database,
        Transaction transaction,
        bool create)
    {
        return GetNamedDictionary(
            database,
            transaction,
            LedgerDictionaryKey,
            create);
    }

    private static DBDictionary? GetNamedDictionary(
        Database database,
        Transaction transaction,
        string dictionaryKey,
        bool create)
    {
        var namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForRead);
        if (namedObjects.Contains(dictionaryKey))
        {
            return (DBDictionary)transaction.GetObject(
                namedObjects.GetAt(dictionaryKey),
                OpenMode.ForRead);
        }
        if (!create)
        {
            return null;
        }

        namedObjects.UpgradeOpen();
        var ledger = new DBDictionary();
        namedObjects.SetAt(dictionaryKey, ledger);
        transaction.AddNewlyCreatedDBObject(ledger, true);
        return ledger;
    }
}

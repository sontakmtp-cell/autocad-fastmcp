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
        var namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForRead);
        if (namedObjects.Contains(LedgerDictionaryKey))
        {
            return (DBDictionary)transaction.GetObject(
                namedObjects.GetAt(LedgerDictionaryKey),
                OpenMode.ForRead);
        }
        if (!create)
        {
            return null;
        }

        namedObjects.UpgradeOpen();
        var ledger = new DBDictionary();
        namedObjects.SetAt(LedgerDictionaryKey, ledger);
        transaction.AddNewlyCreatedDBObject(ledger, true);
        return ledger;
    }
}

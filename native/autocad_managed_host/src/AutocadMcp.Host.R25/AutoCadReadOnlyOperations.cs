using System.Text.Json;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using Document = Autodesk.AutoCAD.ApplicationServices.Document;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadReadOnlyOperations(
    AutoCadIdleScheduler scheduler,
    DocumentIdentityRegistry identities,
    string packageHash) : IReadOnlyHostOperations
{
    private readonly AutoCadEntitySnapshotOperations _entityOperations = new(identities);

    public Task<object> GetHandshakeEvidenceAsync(CancellationToken cancellationToken) =>
        scheduler.RunAsync<object>(GetHandshakeEvidence, cancellationToken);

    private object GetHandshakeEvidence()
    {
        var document = Application.DocumentManager.MdiActiveDocument;
        return new
        {
            host_family = HostConstants.HostFamily,
            host_version = HostConstants.HostVersion,
            package_id = HostConstants.PackageId,
            package_version = HostConstants.PackageVersion,
            package_hash = packageHash,
            product = GetProductName(),
            edition = "full",
            release_year = 2025,
            series = "R25.0",
            active_document_id = document is null ? null : identities.Get(document).DocumentId,
            capabilities = new[]
            {
                "host.health",
                "observe.summary",
                "entity.snapshot.v2",
                "document.events.v1",
                "cad.program.v0",
                "preview.database_abort.v1"
            }
        };
    }

    public Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken) =>
        scheduler.RunAsync<object>(() => command.OperationId switch
        {
            "host.health" => GetHealth(),
            "drawing.observe.summary" => Observe(command),
            "entity.snapshot.page" => _entityOperations.ReadPage(command),
            "document.events.summary" => _entityOperations.ReadEvents(command),
            _ => throw new ProtocolValidationException("capability_missing", "Operation is not registered.")
        }, cancellationToken);

    private object GetHealth()
    {
        var document = Application.DocumentManager.MdiActiveDocument;
        var commandActive = GetCommandActive();
        return new
        {
            status = document is null
                ? "no_document"
                : commandActive == 0
                    ? "ready"
                    : (commandActive & 8) != 0
                        ? "modal_dialog"
                        : "busy",
            product = GetProductName(),
            edition = "full",
            release_year = 2025,
            series = "R25.0",
            active_document_id = document is null ? null : identities.Get(document).DocumentId,
            active_document_name = document is null ? null : Path.GetFileName(document.Name),
            is_quiescent = commandActive == 0,
            is_modal_dialog = (commandActive & 8) != 0,
            capabilities = new[]
            {
                "host.health",
                "observe.summary",
                "entity.snapshot.v2",
                "document.events.v1",
                "cad.program.v0",
                "preview.database_abort.v1"
            }
        };
    }

    private object Observe(CommandRequest command)
    {
        var document = Application.DocumentManager.MdiActiveDocument
            ?? throw new ProtocolValidationException("no_active_document", "No active drawing is open.");
        var commandActive = GetCommandActive();
        if ((commandActive & 8) != 0)
        {
            throw new ProtocolValidationException(
                "modal_dialog_active",
                "AutoCAD is waiting for a modal dialog.");
        }
        if (commandActive != 0)
        {
            throw new ProtocolValidationException("autocad_busy", "AutoCAD is executing another command.");
        }

        var identity = identities.Get(document);
        var documentId = identity.DocumentId;
        if (command.DocumentId is not null && command.DocumentId != documentId)
        {
            throw new ProtocolValidationException("active_document_changed", "The active document changed.");
        }

        var includeLayers = !command.Arguments.TryGetProperty("include_layers", out var include) ||
            include.GetBoolean();
        var maxLayers = command.Arguments.TryGetProperty("max_layers", out var max)
            ? max.GetInt32()
            : 256;
        var revisionBefore = identity.Revision.Snapshot(DateTimeOffset.UtcNow);

        using var transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
        var blockTable = (BlockTable)transaction.GetObject(document.Database.BlockTableId, OpenMode.ForRead);
        var modelSpace = (BlockTableRecord)transaction.GetObject(
            blockTable[BlockTableRecord.ModelSpace],
            OpenMode.ForRead);
        var entityCount = modelSpace.Cast<ObjectId>().Count();

        var layerTable = (LayerTable)transaction.GetObject(document.Database.LayerTableId, OpenMode.ForRead);
        var allLayers = new List<string>();
        foreach (var layerId in layerTable)
        {
            var layer = (LayerTableRecord)transaction.GetObject(layerId, OpenMode.ForRead);
            allLayers.Add(Bound(layer.Name, 255));
        }
        allLayers.Sort(StringComparer.OrdinalIgnoreCase);
        if (!ReferenceEquals(Application.DocumentManager.MdiActiveDocument, document))
        {
            throw new ProtocolValidationException("active_document_changed", "The active document changed.");
        }
        identity.Revision.AssertRevision(revisionBefore.Revision, DateTimeOffset.UtcNow);

        return new
        {
            document_id = documentId,
            document_name = Bound(Path.GetFileName(document.Name), 255),
            database_fingerprint = identity.DatabaseFingerprint,
            revision = revisionBefore,
            entity_count = entityCount,
            layer_count = allLayers.Count,
            layers = includeLayers ? allLayers.Take(maxLayers).ToArray() : [],
            layers_truncated = includeLayers && allLayers.Count > maxLayers,
            product = GetProductName(),
            edition = "full",
            release_year = 2025,
            series = "R25.0"
        };
    }

    private static int GetCommandActive()
    {
        try
        {
            return Convert.ToInt32(Application.GetSystemVariable("CMDACTIVE"));
        }
        catch
        {
            return 1;
        }
    }

    private static string GetProductName()
    {
        try
        {
            return Bound(Convert.ToString(Application.GetSystemVariable("PRODUCT")) ?? "AutoCAD", 128);
        }
        catch
        {
            return "AutoCAD";
        }
    }

    private static string Bound(string value, int maximum) =>
        value.Length <= maximum ? value : value[..maximum];
}

using System.Text.Json;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Application = Autodesk.AutoCAD.ApplicationServices.Core.Application;
using Document = Autodesk.AutoCAD.ApplicationServices.Document;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadReadOnlyOperations(
    AutoCadIdleScheduler scheduler,
    DocumentIdentityRegistry identities,
    string packageHash,
    Phase8HostRuntimeEvidence phase8Runtime,
    bool phase8SourceEnabled,
    bool phase8CreatePackEnabled,
    bool phase8TransformPackEnabled,
    bool phase8CheckpointV2Enabled) : IReadOnlyHostOperations
{
    private readonly AutoCadEntitySnapshotOperations _entityOperations = new(identities);
    private bool Phase8Enabled =>
        phase8SourceEnabled &&
        (phase8CreatePackEnabled || phase8TransformPackEnabled);

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
            capabilities = Capabilities(),
            capability_states = Phase8CapabilityStates(),
            phase8_host_evidence = Phase8Enabled
                ? new
                {
                    schema_version = "cad.host-capability-evidence/1",
                    runtime_id = phase8Runtime.RuntimeId,
                    runtime_role = phase8Runtime.RuntimeRole,
                    host_family = phase8Runtime.HostFamily,
                    host_version = phase8Runtime.HostVersion,
                    package_id = phase8Runtime.PackageId,
                    package_version = phase8Runtime.PackageVersion,
                    package_hash = phase8Runtime.PackageHash,
                    operation_registry_version =
                        phase8Runtime.OperationRegistryVersion,
                    operation_registry_hash =
                        phase8Runtime.OperationRegistryHash,
                    host_evidence_digest =
                        phase8Runtime.HostEvidenceDigest
                }
                : null
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
        var identity = document is null ? null : identities.Get(document);
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
            active_document_id = identity?.DocumentId,
            active_document_revision = identity?.Revision
                .Snapshot(DateTimeOffset.UtcNow)
                .Revision.ToString(System.Globalization.CultureInfo.InvariantCulture),
            active_document_name = document is null ? null : Path.GetFileName(document.Name),
            is_quiescent = commandActive == 0,
            is_modal_dialog = (commandActive & 8) != 0,
            capabilities = Capabilities(),
            capability_states = Phase8CapabilityStates(),
            phase8_host_evidence_digest = Phase8Enabled
                ? phase8Runtime.HostEvidenceDigest
                : null
        };
    }

    private string[] Capabilities() =>
        new[]
        {
            "host.health",
            "observe.summary",
            "entity.snapshot.v2",
            "entity.geometry.arc/1",
            "entity.geometry.circle/1",
            "entity.geometry.line/1",
            "entity.geometry.polyline/1",
            "document.events.v1",
            "cad.program.v0.2",
            "cad.program.preview",
            "cad.program.commit",
            "cad.program.validate",
            "cad.recovery.receipt_query",
            "cad.rollback.checkpoint.lookup",
            "cad.rollback.preview",
            "cad.rollback.commit",
            "cad.rollback.validate",
            "preview.database_abort.v1",
            "durable.receipt.v2"
        }
        .Concat(Phase8CapabilityStates().Keys)
        .Distinct(StringComparer.Ordinal)
        .Order(StringComparer.Ordinal)
        .ToArray();

    private Dictionary<string, string> Phase8CapabilityStates()
    {
        var states = new Dictionary<string, string>(StringComparer.Ordinal);
        if (!Phase8Enabled)
        {
            return states;
        }
        Add("cad.program.v1.preview");
        Add("cad.program.v1.commit");
        Add("cad.program.v1.compile");
        Add("cad.validation.geometry.basic.v1");
        Add("cad.validation.document.revision.v1");
        Add("cad.validation.layer.exists.v1");
        Add("cad.validation.entity.fingerprint.v1");
        Add("cad.validation.transform.result.v1");
        Add("cad.validation.rollback.eligibility.v1");

        if (phase8CreatePackEnabled)
        {
            AddEntityPack("copy");
            AddEntityPack("offset");
        }
        if (phase8TransformPackEnabled && phase8CheckpointV2Enabled)
        {
            AddEntityPack("move");
            foreach (var entity in new[] { "line", "circle", "lwpolyline" })
            {
                Add($"cad.rollback.checkpoint.v2.{entity}");
            }
        }
        return states;

        void AddEntityPack(string operation)
        {
            foreach (var entity in new[] { "line", "circle", "lwpolyline" })
            {
                Add($"cad.op.{operation}.{entity}.v1");
            }
        }

        void Add(string capability) => states[capability] = "lab_commit";
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

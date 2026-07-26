using AutocadMcp.Host.Core;

namespace AutocadMcp.Host.R25;

/// <summary>
/// Bounded composition hook for the existing observation host. The bootstrap
/// creates this wrapper once and still exposes a single cad.host/1 dispatcher.
/// </summary>
internal sealed class CadProgramHostOperations(
    IReadOnlyHostOperations observations,
    AutoCadProgramOperations programs) : IReadOnlyHostOperations
{
    public Task<object> GetHandshakeEvidenceAsync(CancellationToken cancellationToken) =>
        observations.GetHandshakeEvidenceAsync(cancellationToken);

    public Task<object> ExecuteAsync(CommandRequest command, CancellationToken cancellationToken) =>
        CadProgramContract.OperationIds.Contains(command.OperationId)
            ? programs.ExecuteAsync(command, cancellationToken)
            : observations.ExecuteAsync(command, cancellationToken);
}

using System.IO.Pipes;

namespace AutocadMcp.Host.Core;

public sealed class LocalPipeServer(
    string pipeName,
    Func<HostSession> sessionFactory,
    Action<string>? log = null) : IAsyncDisposable
{
    private readonly CancellationTokenSource _shutdown = new();
    private Task? _serverTask;

    public void Start()
    {
        if (_serverTask is not null)
        {
            throw new InvalidOperationException("Pipe server is already running.");
        }
        _serverTask = RunAsync(_shutdown.Token);
    }

    public async ValueTask DisposeAsync()
    {
        _shutdown.Cancel();
        if (_serverTask is not null)
        {
            try
            {
                await _serverTask.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }
        _shutdown.Dispose();
    }

    private async Task RunAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            await using var pipe = new NamedPipeServerStream(
                pipeName,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly,
                HostProtocol.MaxFrameBytes,
                HostProtocol.MaxFrameBytes);
            await pipe.WaitForConnectionAsync(cancellationToken).ConfigureAwait(false);
            var session = sessionFactory();
            try
            {
                while (pipe.IsConnected && !cancellationToken.IsCancellationRequested)
                {
                    var request = await FrameCodec.ReadAsync(pipe, cancellationToken).ConfigureAwait(false);
                    if (request is null)
                    {
                        break;
                    }
                    var response = await session.HandleAsync(request, cancellationToken).ConfigureAwait(false);
                    await FrameCodec.WriteAsync(pipe, response, cancellationToken).ConfigureAwait(false);
                }
            }
            catch (Exception exception) when (
                exception is IOException or EndOfStreamException or ProtocolValidationException)
            {
                log?.Invoke($"Local pipe session ended: {exception.GetType().Name}");
            }
            // A disconnect ends the session. The Host never replays or retries a command.
        }
    }
}

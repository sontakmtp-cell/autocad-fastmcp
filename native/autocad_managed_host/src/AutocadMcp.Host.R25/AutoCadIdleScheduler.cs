using Autodesk.AutoCAD.ApplicationServices.Core;
using AutocadMcp.Host.Core;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadIdleScheduler : IDisposable
{
    private readonly IdleWorkQueue _queue = new();
    private bool _disposed;

    public AutoCadIdleScheduler() => Application.Idle += OnIdle;

    public Task<T> RunAsync<T>(Func<T> action, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        return _queue.Enqueue(action, cancellationToken);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        Application.Idle -= OnIdle;
        _queue.Dispose();
    }

    private void OnIdle(object? sender, EventArgs args)
    {
        _queue.Process(8);
    }
}

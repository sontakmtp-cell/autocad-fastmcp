using System.Collections.Concurrent;
using Autodesk.AutoCAD.ApplicationServices.Core;

namespace AutocadMcp.Host.R25;

internal sealed class AutoCadIdleScheduler : IDisposable
{
    private readonly ConcurrentQueue<IWorkItem> _queue = new();
    private bool _disposed;

    public AutoCadIdleScheduler() => Application.Idle += OnIdle;

    public Task<T> RunAsync<T>(Func<T> action, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        var item = new WorkItem<T>(action, cancellationToken);
        _queue.Enqueue(item);
        return item.Task;
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        Application.Idle -= OnIdle;
        while (_queue.TryDequeue(out var item))
        {
            item.Cancel();
        }
    }

    private void OnIdle(object? sender, EventArgs args)
    {
        var processed = 0;
        while (processed < 8 && _queue.TryDequeue(out var item))
        {
            item.Execute();
            processed++;
        }
    }

    private interface IWorkItem
    {
        void Execute();
        void Cancel();
    }

    private sealed class WorkItem<T>(Func<T> action, CancellationToken cancellationToken) : IWorkItem
    {
        private readonly TaskCompletionSource<T> _completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task<T> Task => _completion.Task;

        public void Execute()
        {
            if (cancellationToken.IsCancellationRequested)
            {
                _completion.TrySetCanceled(cancellationToken);
                return;
            }
            try
            {
                _completion.TrySetResult(action());
            }
            catch (Exception exception)
            {
                _completion.TrySetException(exception);
            }
        }

        public void Cancel() => _completion.TrySetCanceled();
    }
}

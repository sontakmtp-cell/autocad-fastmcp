using System.Collections.Concurrent;

namespace AutocadMcp.Host.Core;

public sealed class IdleWorkQueue : IDisposable
{
    private readonly object _gate = new();
    private readonly ConcurrentQueue<IWorkItem> _queue = new();
    private bool _disposed;

    public Task<T> Enqueue<T>(Func<T> action, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(action);

        lock (_gate)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            var item = new WorkItem<T>(action, cancellationToken);
            _queue.Enqueue(item);
            return item.Task;
        }
    }

    public int Process(int maxItems)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(maxItems);

        var processed = 0;
        while (processed < maxItems && _queue.TryDequeue(out var item))
        {
            item.Execute();
            processed++;
        }
        return processed;
    }

    public void Dispose()
    {
        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }
            _disposed = true;
        }

        while (_queue.TryDequeue(out var item))
        {
            item.Cancel();
        }
    }

    private interface IWorkItem
    {
        void Execute();
        void Cancel();
    }

    private sealed class WorkItem<T> : IWorkItem
    {
        private readonly Func<T> _action;
        private readonly CancellationToken _cancellationToken;
        private readonly TaskCompletionSource<T> _completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        private CancellationTokenRegistration _registration;
        private int _state;

        public WorkItem(Func<T> action, CancellationToken cancellationToken)
        {
            _action = action;
            _cancellationToken = cancellationToken;
            _registration = cancellationToken.UnsafeRegister(
                static state => ((WorkItem<T>)state!).CancelFromToken(),
                this);

            if (Volatile.Read(ref _state) != 0)
            {
                _registration.Unregister();
            }
        }

        public Task<T> Task => _completion.Task;

        public void Execute()
        {
            if (Interlocked.CompareExchange(ref _state, 1, 0) != 0)
            {
                return;
            }

            try
            {
                _completion.TrySetResult(_action());
            }
            catch (Exception exception)
            {
                _completion.TrySetException(exception);
            }
            finally
            {
                Volatile.Write(ref _state, 2);
                _registration.Unregister();
            }
        }

        public void Cancel()
        {
            if (Interlocked.CompareExchange(ref _state, 2, 0) == 0)
            {
                _completion.TrySetCanceled();
                _registration.Unregister();
            }
        }

        private void CancelFromToken()
        {
            if (Interlocked.CompareExchange(ref _state, 2, 0) == 0)
            {
                _completion.TrySetCanceled(_cancellationToken);
                _registration.Unregister();
            }
        }
    }
}

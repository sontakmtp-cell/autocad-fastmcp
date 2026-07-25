using AutocadMcp.Host.Core;
using Xunit;

namespace AutocadMcp.Host.Core.Tests;

public sealed class IdleWorkQueueTests
{
    [Fact]
    public async Task Cancellation_CompletesWithoutIdleProcessing()
    {
        using var queue = new IdleWorkQueue();
        using var cancellation = new CancellationTokenSource();
        var executed = false;
        var task = queue.Enqueue(
            () =>
            {
                executed = true;
                return 1;
            },
            cancellation.Token);

        cancellation.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
        Assert.False(executed);
    }

    [Fact]
    public async Task DeadlineCancellation_CompletesWithoutIdleProcessing()
    {
        using var queue = new IdleWorkQueue();
        using var cancellation = new CancellationTokenSource(
            TimeSpan.FromMilliseconds(25));
        var task = queue.Enqueue(() => 1, cancellation.Token);

        await Assert.ThrowsAnyAsync<OperationCanceledException>(
            () => task.WaitAsync(TimeSpan.FromSeconds(2)));
    }

    [Fact]
    public void Process_SkipsAnAlreadyCanceledItem()
    {
        using var queue = new IdleWorkQueue();
        using var cancellation = new CancellationTokenSource();
        var executed = false;
        _ = queue.Enqueue(
            () =>
            {
                executed = true;
                return 1;
            },
            cancellation.Token);
        cancellation.Cancel();

        var processed = queue.Process(8);

        Assert.Equal(1, processed);
        Assert.False(executed);
    }

    [Fact]
    public async Task Dispose_CancelsPendingWorkWithoutIdleProcessing()
    {
        var queue = new IdleWorkQueue();
        var task = queue.Enqueue(() => 1, CancellationToken.None);

        queue.Dispose();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => task);
        Assert.Throws<ObjectDisposedException>(() =>
        {
            _ = queue.Enqueue(() => 2, CancellationToken.None);
        });
    }

    [Fact]
    public async Task Process_ExecutesPendingWork()
    {
        using var queue = new IdleWorkQueue();
        var task = queue.Enqueue(() => 42, CancellationToken.None);

        var processed = queue.Process(8);

        Assert.Equal(1, processed);
        Assert.Equal(42, await task);
    }
}

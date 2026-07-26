using System.Text.Json;

namespace AutocadMcp.Host.Core;

public sealed record EntitySnapshotRequest(
    int Cursor,
    int Limit,
    int MaxScan,
    long? ExpectedRevision,
    IReadOnlySet<string> Types,
    IReadOnlySet<string> Layers,
    string Space)
{
    public static EntitySnapshotRequest Parse(JsonElement arguments)
    {
        var allowed = new HashSet<string>(StringComparer.Ordinal)
        {
            "cursor", "limit", "max_scan", "expected_revision", "types", "layers", "space"
        };
        if (arguments.EnumerateObject().Any(property => !allowed.Contains(property.Name)))
        {
            throw Invalid("Unknown entity snapshot argument.");
        }

        var cursor = ReadInt(arguments, "cursor", 0);
        var limit = ReadInt(arguments, "limit", 100);
        var maxScan = ReadInt(arguments, "max_scan", 5_000);
        if (cursor is < 0 or > 10_000_000 ||
            limit is < 1 or > 200 ||
            maxScan is < 1 or > 20_000)
        {
            throw Invalid("Entity cursor, limit, or scan budget is outside the allowed range.");
        }

        long? expectedRevision = null;
        if (arguments.TryGetProperty("expected_revision", out var revision))
        {
            if (!revision.TryGetInt64(out var value) || value < 1)
            {
                throw Invalid("expected_revision must be a positive integer.");
            }
            expectedRevision = value;
        }

        var types = ReadStringSet(arguments, "types", 32, 64);
        var layers = ReadStringSet(arguments, "layers", 32, 255);
        var space = arguments.TryGetProperty("space", out var requestedSpace)
            ? requestedSpace.GetString()
            : "all";
        if (space is not ("all" or "model" or "paper"))
        {
            throw Invalid("space must be all, model, or paper.");
        }

        return new(cursor, limit, maxScan, expectedRevision, types, layers, space);
    }

    private static int ReadInt(JsonElement arguments, string name, int defaultValue)
    {
        if (!arguments.TryGetProperty(name, out var property))
        {
            return defaultValue;
        }
        if (!property.TryGetInt32(out var value))
        {
            throw Invalid($"{name} must be an integer.");
        }
        return value;
    }

    private static IReadOnlySet<string> ReadStringSet(
        JsonElement arguments,
        string name,
        int maximumItems,
        int maximumLength)
    {
        if (!arguments.TryGetProperty(name, out var property))
        {
            return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        }
        if (property.ValueKind != JsonValueKind.Array ||
            property.GetArrayLength() > maximumItems)
        {
            throw Invalid($"{name} is not a bounded array.");
        }

        var values = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var item in property.EnumerateArray())
        {
            var value = item.ValueKind == JsonValueKind.String ? item.GetString() : null;
            if (string.IsNullOrWhiteSpace(value) || value.Length > maximumLength)
            {
                throw Invalid($"{name} contains an invalid value.");
            }
            values.Add(value);
        }
        return values;
    }

    private static ProtocolValidationException Invalid(string message) =>
        new("invalid_envelope", message);
}

public sealed record DocumentEventsRequest(long AfterSequence, int MaxEvents, long? ExpectedRevision)
{
    public static DocumentEventsRequest Parse(JsonElement arguments)
    {
        var allowed = new HashSet<string>(StringComparer.Ordinal)
        {
            "after_sequence", "max_events", "expected_revision"
        };
        if (arguments.EnumerateObject().Any(property => !allowed.Contains(property.Name)))
        {
            throw new ProtocolValidationException(
                "invalid_envelope",
                "Unknown document events argument.");
        }

        var afterSequence = ReadLong(arguments, "after_sequence", 0);
        var requestedMaxEvents = ReadLong(arguments, "max_events", 50);
        long? expectedRevision = arguments.TryGetProperty("expected_revision", out _)
            ? ReadLong(arguments, "expected_revision", 0)
            : null;
        if (afterSequence < 0 ||
            requestedMaxEvents is < 1 or > 100 ||
            expectedRevision is < 1)
        {
            throw new ProtocolValidationException(
                "invalid_envelope",
                "Document event cursor, limit, or revision is outside the allowed range.");
        }
        var maxEvents = (int)requestedMaxEvents;
        return new(afterSequence, maxEvents, expectedRevision);
    }

    private static long ReadLong(JsonElement arguments, string name, long defaultValue)
    {
        if (!arguments.TryGetProperty(name, out var property))
        {
            return defaultValue;
        }
        if (!property.TryGetInt64(out var value))
        {
            throw new ProtocolValidationException("invalid_envelope", $"{name} must be an integer.");
        }
        return value;
    }
}

using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using AutocadMcp.Host.Core;
using Autodesk.AutoCAD.ApplicationServices.Core;
using Autodesk.AutoCAD.Runtime;

namespace AutocadMcp.Host.R25;

public sealed class ManagedHostExtension : IExtensionApplication
{
    private static ManagedHostExtension? _instance;
    private readonly byte[] _identityKey = RandomNumberGenerator.GetBytes(32);
    private BootstrapDescriptor? _bootstrap;
    private DocumentEventTracker? _documentEvents;
    private AutoCadIdleScheduler? _scheduler;
    private LocalPipeServer? _server;

    internal static string Status =>
        _instance?._server is null
            ? "Managed Host is not ready."
            : $"Managed Host {HostConstants.HostFamily} {HostConstants.HostVersion}; cad.host/1 local pipe ready.";

    public void Initialize()
    {
        try
        {
            var packageHash = ReadPackageHash();
            _bootstrap = BootstrapDescriptor.Create(packageHash);
            _scheduler = new AutoCadIdleScheduler();
            var identities = new DocumentIdentityRegistry(_identityKey);
            _documentEvents = new DocumentEventTracker(
                Application.DocumentManager,
                identities);
            var observations = new AutoCadReadOnlyOperations(
                _scheduler,
                identities,
                packageHash);
            var programs = new AutoCadProgramOperations(
                _scheduler,
                identities,
                packageHash);
            var operations = new CadProgramHostOperations(observations, programs);
            var evidence = new RuntimeEvidence(
                "managed_dotnet",
                "primary",
                HostConstants.HostFamily,
                HostConstants.HostVersion,
                packageHash);
            _server = new LocalPipeServer(
                _bootstrap.PipeName,
                () => new HostSession(_bootstrap.Secret, operations, evidence),
                WriteDiagnostic);
            _server.Start();
            _instance = this;
            WriteDiagnostic(Status);
        }
        catch (System.Exception exception)
        {
            WriteDiagnostic($"Managed Host failed to initialize: {exception.GetType().Name}");
            Terminate();
        }
    }

    public void Terminate()
    {
        _instance = null;
        if (_server is not null)
        {
            _server.DisposeAsync().AsTask().GetAwaiter().GetResult();
            _server = null;
        }
        _scheduler?.Dispose();
        _scheduler = null;
        _documentEvents?.Dispose();
        _documentEvents = null;
        _bootstrap?.Dispose();
        _bootstrap = null;
        CryptographicOperations.ZeroMemory(_identityKey);
    }

    private static string ReadPackageHash()
    {
        var assemblyDirectory = Path.GetDirectoryName(
            typeof(ManagedHostExtension).Assembly.Location)
            ?? throw new InvalidOperationException("Host assembly location is unavailable.");
        var sharedManifest = Path.GetFullPath(Path.Combine(
            assemblyDirectory,
            "..",
            "Shared",
            "package-manifest.json"));
        if (!File.Exists(sharedManifest))
        {
            throw new InvalidOperationException("Package manifest is missing.");
        }
        var bytes = File.ReadAllBytes(sharedManifest);
        using var document = JsonDocument.Parse(bytes);
        var declared = document.RootElement.GetProperty("package_hash").GetString();
        if (declared is null ||
            declared.Length != 71 ||
            !declared.StartsWith("sha256:", StringComparison.Ordinal))
        {
            throw new InvalidOperationException("Package hash is invalid.");
        }

        var artifactRoot = assemblyDirectory;
        var artifacts = document.RootElement.GetProperty("artifacts");
        var verified = new SortedDictionary<string, string>(StringComparer.Ordinal);
        foreach (var artifact in artifacts.EnumerateObject())
        {
            if (Path.GetFileName(artifact.Name) != artifact.Name)
            {
                throw new InvalidOperationException("Package artifact name is invalid.");
            }
            var expected = artifact.Value.GetString();
            var artifactPath = Path.Combine(artifactRoot, artifact.Name);
            if (expected is null || expected.Length != 64 || !File.Exists(artifactPath))
            {
                throw new InvalidOperationException("Package artifact is missing.");
            }
            var actual = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(artifactPath))).ToLowerInvariant();
            if (!CryptographicOperations.FixedTimeEquals(
                    Encoding.ASCII.GetBytes(expected),
                    Encoding.ASCII.GetBytes(actual)))
            {
                throw new InvalidOperationException("Package artifact hash mismatch.");
            }
            verified[artifact.Name] = actual;
        }

        var aggregateText = string.Join(
            "\n",
            verified.Select(item => $"{item.Key}:{item.Value}"));
        var aggregate = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(aggregateText)))
            .ToLowerInvariant();
        if (!CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(declared[7..]),
                Encoding.ASCII.GetBytes(aggregate)))
        {
            throw new InvalidOperationException("Package aggregate hash mismatch.");
        }
        return declared;
    }

    private static void WriteDiagnostic(string message)
    {
        try
        {
            Application.DocumentManager.MdiActiveDocument?.Editor.WriteMessage($"\nAutoCAD MCP: {message}");
        }
        catch
        {
        }
    }
}

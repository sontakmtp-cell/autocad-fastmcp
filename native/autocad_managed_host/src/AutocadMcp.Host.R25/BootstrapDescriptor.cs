using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json;
using AutocadMcp.Host.Core;

namespace AutocadMcp.Host.R25;

internal sealed class BootstrapDescriptor : IDisposable
{
    private readonly string _path;

    private BootstrapDescriptor(string path, string pipeName, byte[] secret)
    {
        _path = path;
        PipeName = pipeName;
        Secret = secret;
    }

    public string PipeName { get; }
    public byte[] Secret { get; }

    public static BootstrapDescriptor Create(string packageHash)
    {
        if (!OperatingSystem.IsWindows())
        {
            throw new PlatformNotSupportedException("Managed Host requires Windows.");
        }

        var sid = WindowsIdentity.GetCurrent().User
            ?? throw new InvalidOperationException("Current Windows SID is unavailable.");
        var directory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "KythuatVang",
            "AutoCADMcp");
        Directory.CreateDirectory(directory);
        RestrictDirectory(directory, sid);

        var secret = RandomNumberGenerator.GetBytes(32);
        var pipeName = $"kythuatvang.autocad-mcp.r25.{Environment.ProcessId}.{Convert.ToHexString(RandomNumberGenerator.GetBytes(12)).ToLowerInvariant()}";
        var path = Path.Combine(directory, HostConstants.BootstrapFileName);
        var temporaryPath = path + $".{Environment.ProcessId}.tmp";
        var descriptor = new
        {
            protocol_version = HostProtocol.Version,
            pipe_name = pipeName,
            session_secret_base64 = Convert.ToBase64String(secret),
            host_pid = Environment.ProcessId,
            host_family = HostConstants.HostFamily,
            host_version = HostConstants.HostVersion,
            package_hash = packageHash,
            created_at = DateTimeOffset.UtcNow.ToString("O")
        };
        File.WriteAllBytes(temporaryPath, JsonSerializer.SerializeToUtf8Bytes(descriptor, HostProtocol.JsonOptions));
        RestrictFile(temporaryPath, sid);
        File.Move(temporaryPath, path, true);
        RestrictFile(path, sid);
        return new BootstrapDescriptor(path, pipeName, secret);
    }

    public void Dispose()
    {
        CryptographicOperations.ZeroMemory(Secret);
        try
        {
            if (File.Exists(_path))
            {
                File.Delete(_path);
            }
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static void RestrictDirectory(string path, SecurityIdentifier sid)
    {
        var security = new DirectorySecurity();
        security.SetAccessRuleProtection(true, false);
        security.SetOwner(sid);
        security.AddAccessRule(new FileSystemAccessRule(
            sid,
            FileSystemRights.FullControl,
            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
            PropagationFlags.None,
            AccessControlType.Allow));
        new DirectoryInfo(path).SetAccessControl(security);
    }

    private static void RestrictFile(string path, SecurityIdentifier sid)
    {
        var security = new FileSecurity();
        security.SetAccessRuleProtection(true, false);
        security.SetOwner(sid);
        security.AddAccessRule(new FileSystemAccessRule(
            sid,
            FileSystemRights.FullControl,
            AccessControlType.Allow));
        new FileInfo(path).SetAccessControl(security);
    }
}

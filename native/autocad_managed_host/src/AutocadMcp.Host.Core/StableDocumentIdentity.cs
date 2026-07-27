using System.Security.Cryptography;
using System.Text;

namespace AutocadMcp.Host.Core;

public static class StableDocumentIdentity
{
    public static string FromDatabaseFingerprint(string fingerprint)
    {
        if (string.IsNullOrWhiteSpace(fingerprint) ||
            fingerprint.Length > 128 ||
            fingerprint == "unavailable")
        {
            throw new ProtocolValidationException(
                "program_invalid",
                "A stable database fingerprint is required for managed write.");
        }
        var digest = SHA256.HashData(
            Encoding.UTF8.GetBytes($"cad.document/1\n{fingerprint.ToLowerInvariant()}"));
        return $"doc-{Convert.ToHexString(digest).ToLowerInvariant()[..24]}";
    }
}

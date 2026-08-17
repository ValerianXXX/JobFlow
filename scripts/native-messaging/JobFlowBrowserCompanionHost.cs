using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

internal sealed class HostError : Exception
{
    internal string Code { get; private set; }

    internal HostError(string code, string message) : base(message)
    {
        Code = code;
    }
}

internal static class JobFlowBrowserCompanionHost
{
    private const int SchemaVersion = 1;
    private const int ProtocolVersion = 2;
    private const int MaximumRequestBytes = 4096;
    private const int MaximumFileBytes = 16384;
    private const string HostManifestName = "com.jobflow.browser_companion.json";
    private const string BindingFileName = "browser-companion-binding.json";
    private static readonly Regex ExtensionOrigin = new Regex(
        @"^chrome-extension://[a-p]{32}/$", RegexOptions.CultureInvariant);
    private static readonly Regex InstallationId = new Regex(
        @"^[a-f0-9]{32}$", RegexOptions.CultureInvariant);
    private static readonly Regex Base64Url = new Regex(
        @"^[A-Za-z0-9_-]+$", RegexOptions.CultureInvariant);

    public static int Main(string[] args)
    {
        try
        {
            Dictionary<string, object> result = Process(args);
            WriteResponse(result);
        }
        catch (HostError error)
        {
            WriteResponse(new Dictionary<string, object>
            {
                {"status", "BLOCKED"},
                {"code", error.Code},
                {"message", error.Message},
                {"automatic_retry", false}
            });
        }
        catch
        {
            WriteResponse(new Dictionary<string, object>
            {
                {"status", "BLOCKED"},
                {"code", "COMPANION_NATIVE_HOST_FAILED"},
                {"message", "Repair the local JobFlow installation before reconnecting the Browser Companion."},
                {"automatic_retry", false}
            });
        }
        return 0;
    }

    private static Dictionary<string, object> Process(string[] args)
    {
        string caller = NormalizeCaller(args);
        string executable = Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
        string hostRoot = Path.GetDirectoryName(executable);
        string jobFlowRoot = Directory.GetParent(hostRoot).FullName;
        string hostManifestPath = Path.Combine(hostRoot, HostManifestName);
        string bindingPath = Path.Combine(jobFlowRoot, BindingFileName);

        RequirePrivatePath(hostRoot, jobFlowRoot);
        RequirePrivatePath(executable, jobFlowRoot);
        RequirePrivatePath(hostManifestPath, jobFlowRoot);
        RequirePrivatePath(bindingPath, jobFlowRoot);

        Dictionary<string, object> hostManifest = ReadObject(hostManifestPath, "COMPANION_NATIVE_HOST_INVALID");
        if (!HasExactKeys(hostManifest, "name", "description", "path", "type", "allowed_origins") ||
            !String.Equals(Convert.ToString(hostManifest["name"]), "com.jobflow.browser_companion", StringComparison.Ordinal) ||
            !String.Equals(Convert.ToString(hostManifest["type"]), "stdio", StringComparison.Ordinal) ||
            !AllowedOrigins(hostManifest["allowed_origins"]).Contains(caller, StringComparer.Ordinal))
        {
            throw new HostError(
                "COMPANION_NATIVE_HOST_ORIGIN_FORBIDDEN",
                "This browser extension is not authorized for the local JobFlow installation.");
        }

        Dictionary<string, object> request = ReadRequest();
        if (!HasExactKeys(request, "schema_version", "type", "protocol_version", "extension_version") ||
            ToInteger(request["schema_version"]) != SchemaVersion ||
            ToInteger(request["protocol_version"]) != ProtocolVersion ||
            !String.Equals(Convert.ToString(request["type"]), "JOBFLOW_GET_INSTALLATION_BINDING", StringComparison.Ordinal) ||
            !Regex.IsMatch(Convert.ToString(request["extension_version"]), @"^[0-9]+\.[0-9]+\.[0-9]+$", RegexOptions.CultureInvariant))
        {
            throw new HostError(
                "COMPANION_NATIVE_HOST_REQUEST_INVALID",
                "The Browser Companion sent an invalid local binding request.");
        }

        Dictionary<string, object> binding = ReadObject(bindingPath, "COMPANION_BINDING_INVALID");
        string installationId = Convert.ToString(binding.ContainsKey("installation_id") ? binding["installation_id"] : null);
        string secret = Convert.ToString(binding.ContainsKey("secret_b64url") ? binding["secret_b64url"] : null);
        if (!HasExactKeys(binding, "schema_version", "installation_id", "secret_b64url") ||
            ToInteger(binding["schema_version"]) != SchemaVersion || !InstallationId.IsMatch(installationId) ||
            !Base64Url.IsMatch(secret) || DecodeBase64Url(secret).Length != 32)
        {
            throw new HostError(
                "COMPANION_BINDING_INVALID",
                "Repair the local JobFlow installation before reconnecting the Browser Companion.");
        }

        return new Dictionary<string, object>
        {
            {"status", "READY"},
            {"schema_version", SchemaVersion},
            {"installation_id", installationId},
            {"secret_b64url", secret}
        };
    }

    private static string NormalizeCaller(string[] args)
    {
        string caller = args != null && args.Length > 0 ? Convert.ToString(args[0]) : String.Empty;
        if (!caller.EndsWith("/", StringComparison.Ordinal)) caller += "/";
        if (!ExtensionOrigin.IsMatch(caller))
        {
            throw new HostError(
                "COMPANION_NATIVE_HOST_ORIGIN_FORBIDDEN",
                "This browser extension is not authorized for the local JobFlow installation.");
        }
        return caller;
    }

    private static Dictionary<string, object> ReadRequest()
    {
        Stream input = Console.OpenStandardInput();
        byte[] prefix = ReadExactly(input, 4);
        int length = BitConverter.ToInt32(prefix, 0);
        if (length < 2 || length > MaximumRequestBytes)
        {
            throw new HostError(
                "COMPANION_NATIVE_HOST_REQUEST_INVALID",
                "The Browser Companion sent an invalid local binding request.");
        }
        byte[] payload = ReadExactly(input, length);
        try
        {
            return Deserialize(Encoding.UTF8.GetString(payload));
        }
        catch
        {
            throw new HostError(
                "COMPANION_NATIVE_HOST_REQUEST_INVALID",
                "The Browser Companion sent an invalid local binding request.");
        }
    }

    private static Dictionary<string, object> ReadObject(string path, string code)
    {
        try
        {
            FileInfo info = new FileInfo(path);
            if (!info.Exists || info.Length < 2 || info.Length > MaximumFileBytes) throw new IOException();
            return Deserialize(File.ReadAllText(path, new UTF8Encoding(false, true)));
        }
        catch (HostError)
        {
            throw;
        }
        catch
        {
            throw new HostError(code, "Repair the local JobFlow installation before reconnecting the Browser Companion.");
        }
    }

    private static Dictionary<string, object> Deserialize(string text)
    {
        JavaScriptSerializer serializer = new JavaScriptSerializer
        {
            MaxJsonLength = MaximumFileBytes,
            RecursionLimit = 8
        };
        Dictionary<string, object> value = serializer.Deserialize<Dictionary<string, object>>(text);
        if (value == null) throw new FormatException();
        return value;
    }

    private static IEnumerable<string> AllowedOrigins(object value)
    {
        IEnumerable sequence = value as IEnumerable;
        if (sequence == null || value is string) return Enumerable.Empty<string>();
        return sequence.Cast<object>().Select(Convert.ToString).Where(origin => ExtensionOrigin.IsMatch(origin));
    }

    private static int ToInteger(object value)
    {
        try
        {
            return Convert.ToInt32(value);
        }
        catch
        {
            return Int32.MinValue;
        }
    }

    private static bool HasExactKeys(Dictionary<string, object> value, params string[] keys)
    {
        return value.Count == keys.Length && value.Keys.OrderBy(item => item, StringComparer.Ordinal)
            .SequenceEqual(keys.OrderBy(item => item, StringComparer.Ordinal), StringComparer.Ordinal);
    }

    private static byte[] DecodeBase64Url(string value)
    {
        try
        {
            string normalized = value.Replace('-', '+').Replace('_', '/');
            normalized += new string('=', (4 - normalized.Length % 4) % 4);
            return Convert.FromBase64String(normalized);
        }
        catch
        {
            return new byte[0];
        }
    }

    private static void RequirePrivatePath(string path, string root)
    {
        string fullPath = Path.GetFullPath(path);
        string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!fullPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase) &&
            !String.Equals(fullPath.TrimEnd(Path.DirectorySeparatorChar), fullRoot.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
        {
            throw new HostError("COMPANION_NATIVE_HOST_PATH_FORBIDDEN", "The local Browser Companion path is not trusted.");
        }

        string cursor = File.Exists(fullPath) ? fullPath : (Directory.Exists(fullPath) ? fullPath : Path.GetDirectoryName(fullPath));
        while (!String.IsNullOrEmpty(cursor))
        {
            if (File.Exists(cursor) || Directory.Exists(cursor))
            {
                FileAttributes attributes = File.GetAttributes(cursor);
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new HostError("COMPANION_NATIVE_HOST_REPARSE_FORBIDDEN", "The local Browser Companion path is not trusted.");
                }
            }
            if (String.Equals(cursor.TrimEnd(Path.DirectorySeparatorChar), fullRoot.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase)) break;
            cursor = Path.GetDirectoryName(cursor);
        }
    }

    private static byte[] ReadExactly(Stream stream, int length)
    {
        byte[] value = new byte[length];
        int offset = 0;
        while (offset < length)
        {
            int read = stream.Read(value, offset, length - offset);
            if (read <= 0)
            {
                throw new HostError(
                    "COMPANION_NATIVE_HOST_REQUEST_INVALID",
                    "The Browser Companion sent an incomplete local binding request.");
            }
            offset += read;
        }
        return value;
    }

    private static void WriteResponse(Dictionary<string, object> value)
    {
        try
        {
            JavaScriptSerializer serializer = new JavaScriptSerializer {MaxJsonLength = MaximumFileBytes, RecursionLimit = 8};
            byte[] payload = Encoding.UTF8.GetBytes(serializer.Serialize(value));
            Stream output = Console.OpenStandardOutput();
            byte[] prefix = BitConverter.GetBytes(payload.Length);
            output.Write(prefix, 0, prefix.Length);
            output.Write(payload, 0, payload.Length);
            output.Flush();
        }
        catch
        {
            Environment.ExitCode = 1;
        }
    }
}

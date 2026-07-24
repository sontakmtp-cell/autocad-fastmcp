using Autodesk.AutoCAD.ApplicationServices.Core;
using Autodesk.AutoCAD.Runtime;

namespace AutocadMcp.Host.R25;

public sealed class SupportCommands
{
    [CommandMethod("AUTOCADMCPSTATUS", CommandFlags.Session)]
    public static void ShowStatus()
    {
        Application.DocumentManager.MdiActiveDocument?.Editor.WriteMessage(
            $"\n{ManagedHostExtension.Status}");
    }
}

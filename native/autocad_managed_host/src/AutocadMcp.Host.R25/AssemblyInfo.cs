using Autodesk.AutoCAD.Runtime;

[assembly: ExtensionApplication(typeof(AutocadMcp.Host.R25.ManagedHostExtension))]
[assembly: CommandClass(typeof(AutocadMcp.Host.R25.SupportCommands))]

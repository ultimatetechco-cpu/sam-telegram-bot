Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = currentDir
WshShell.Run chr(34) & currentDir & "\RUN_AUTOPOST_SERVICE.bat" & chr(34), 0, False
Set WshShell = Nothing

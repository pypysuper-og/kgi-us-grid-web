Option Explicit
Dim shell, files, folder, command, argument
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & folder & "\install.ps1"""
For Each argument In WScript.Arguments
    command = command & " """ & Replace(argument, """", """""") & """"
Next
shell.Run command, 0, False

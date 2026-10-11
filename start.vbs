Option Explicit
Dim shell, files, folder, command, argument
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & folder & "\start.ps1"""
For Each argument In WScript.Arguments
    Select Case LCase(argument)
        Case "--port": argument = "-Port"
        Case "--runtime": argument = "-Runtime"
        Case "--no-browser": argument = "-NoBrowser"
    End Select
    command = command & " """ & Replace(argument, """", """""") & """"
Next
shell.Run command, 0, False

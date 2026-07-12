Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder

venvPythonw = folder & "\.venv\Scripts\pythonw.exe"
venvPython = folder & "\.venv\Scripts\python.exe"

If fso.FileExists(venvPythonw) Then
    pythonCommand = """" & venvPythonw & """"
ElseIf fso.FileExists(venvPython) Then
    pythonCommand = """" & venvPython & """"
Else
    pythonCommand = "pythonw.exe"
    If shell.Run("cmd.exe /d /c where pythonw.exe >nul 2>nul", 0, True) <> 0 Then
        If shell.Run("cmd.exe /d /c where python.exe >nul 2>nul", 0, True) <> 0 Then
            MsgBox "Python was not found. Install Python, then run: pip install -r requirements.txt", 16, "PC Macro Builder"
            WScript.Quit 1
        End If
        pythonCommand = "python.exe"
    End If
End If

shell.Run pythonCommand & " """ & folder & "\launcher.pyw""", 0, False

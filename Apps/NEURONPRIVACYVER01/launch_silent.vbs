' Launch Privacy Verifier with no console window.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
neuronRoot = fso.GetParentFolderName(fso.GetParentFolderName(appDir))
pyw = neuronRoot & "\.venv1\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  pyw = "pythonw.exe"
End If
sh.CurrentDirectory = appDir
' 0 = hidden console; pythonw has none anyway
sh.Run """" & pyw & """ main.py", 0, False

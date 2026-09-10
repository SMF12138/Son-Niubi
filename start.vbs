' start.vbs - windowless entry: Flask + desktop pet + open browser
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = base & "\.venv\Scripts\pythonw.exe"
py = base & "\.venv\Scripts\python.exe"
url = "http://127.0.0.1:8000"

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = base

' start Flask (no window)
shell.Run """" & pyw & """ -m app.cli serve --no-browser", 0, False

' open browser
WScript.Sleep 1000
shell.Run url, 1, False

' start desktop pet
WScript.Sleep 500
shell.Run """" & py & """ floating_pet.py", 0, False

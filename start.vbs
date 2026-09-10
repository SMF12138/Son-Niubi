' start.vbs — 无窗口入口: 起 Flask + 桌宠 + 开浏览器
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = base & "\.venv\Scripts\pythonw.exe"
py = base & "\.venv\Scripts\python.exe"
url = "http://127.0.0.1:8000"

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = base

' 起 Flask (无窗口)
shell.Run """" & pyw & """ -m app.cli serve --no-browser", 0, False

' 开浏览器
WScript.Sleep 1000
shell.Run url, 1, False

' 起桌宠
WScript.Sleep 500
shell.Run """" & py & """ floating_pet.py", 0, False

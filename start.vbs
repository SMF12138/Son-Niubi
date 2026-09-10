' 无窗口启动入口: 桌面快捷方式指向本文件, 隐藏窗口执行 start.bat
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.Run """" & baseDir & "\start.bat""", 0, False
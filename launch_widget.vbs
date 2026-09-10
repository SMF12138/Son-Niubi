' 以隐藏窗口方式启动桌宠 (tkinter 需 python.exe 显示窗口, vbs 隐藏其控制台)
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
' 参数: 完全隐藏窗口(0), waitOnReturn=False
shell.Run """" & baseDir & "\.venv\Scripts\python.exe"" -c ""from floating_pet import FloatingPet; FloatingPet().run()""", 0, False
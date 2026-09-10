' Launch desktop pet with hidden console window
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = baseDir
shell.Run """" & baseDir & "\.venv\Scripts\python.exe"" """ & baseDir & "\floating_pet.py""", 0, False
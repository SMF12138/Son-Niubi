@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "from floating_pet import FloatingPet; FloatingPet().run()"

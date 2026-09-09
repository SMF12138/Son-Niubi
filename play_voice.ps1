Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([Uri]::new($args[0]))
Start-Sleep -Milliseconds 1000
$p.Play()
Start-Sleep -Seconds 4
$p.Close()

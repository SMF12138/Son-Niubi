# 常驻语音播放器: 启动时预加载 m4a(约2s, 后台完成), 之后信号文件触发播放(即时)
Add-Type -AssemblyName PresentationCore

$player = New-Object System.Windows.Media.MediaPlayer
$voiceFile = $args[0]
$signalFile = Join-Path (Split-Path $PSCommandPath) "data\pet\_play_signal.dat"

# 预加载并等待完成(确保切形态时已就绪)
$player.Open([Uri]::new($voiceFile))
Start-Sleep -Milliseconds 2000

# 清理旧信号
if (Test-Path $signalFile) { Remove-Item $signalFile -Force }

# 常驻循环: 出现信号文件即播放
while ($true) {
    if (Test-Path $signalFile) {
        try {
            $player.Stop()
            $player.Play()
        } catch { }
        Remove-Item $signalFile -Force
        Start-Sleep -Milliseconds 400
    }
    Start-Sleep -Milliseconds 60
}
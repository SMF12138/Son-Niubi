import ctypes, time, os
winmm = ctypes.windll.winmm
m4a = os.path.join(os.environ["USERPROFILE"], "Desktop", "语音.m4a")
print("m4a:", m4a, "exists:", os.path.exists(m4a))
for t in ["mpegaudio", "mpegvideo", "waveaudio", "CDAudio", "MPEGVideo", "NONE"]:
    cmd = f'open "{m4a}"' + (f" type {t}" if t != "NONE" else "") + " alias v"
    ret = winmm.mciSendStringW(cmd, None, 0, 0)
    if ret == 0:
        print(f"type='{t}' -> OPEN OK, playing...")
        winmm.mciSendStringW("play v", None, 0, 0)
        time.sleep(3)
        winmm.mciSendStringW("close v", None, 0, 0)
        print("DONE (should have heard sound)")
        break
    else:
        print(f"type='{t}' -> rc={ret}")

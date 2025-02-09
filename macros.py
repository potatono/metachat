import sys

if sys.platform == "win32":
    import voicemeeterlib
    import win32api
    import win32gui
    import win32ui
    import win32con
    from win32con import VK_MEDIA_PLAY_PAUSE, KEYEVENTF_EXTENDEDKEY


from config import *
from logs import *
from obs import ObsApp

class Macros:
    def __init__(self):
        self.log = Logger("macros")
        self.obs = ObsApp()
        self.obs.ensure_connected()
    
    def mute_microphone(self, muted=True):
        if sys.platform == "win32":
            try:
                with voicemeeterlib.api('potato') as vm:
                    vm.strip[0].mute = muted
            except Exception as ex:
                self.log.error("While muting microphone", ex)

    def playpause_music(self):
        if sys.platform == "win32":
            win32api.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY, 0)

    def capture_window(self, window_name, w, h, path):
        if sys.platform != "win32":
            self.log.error("Cannot capture on this platform")
            return
        
        hwnd = win32gui.FindWindow(None, window_name)
        self.log.debug(f"hwnd={hwnd}")
        wDC = win32gui.GetWindowDC(hwnd)
        dcObj=win32ui.CreateDCFromHandle(wDC)
        cDC=dcObj.CreateCompatibleDC()
        dataBitMap = win32ui.CreateBitmap()
        dataBitMap.CreateCompatibleBitmap(dcObj, w, h)
        cDC.SelectObject(dataBitMap)
        cDC.BitBlt((0,0),(w, h) , dcObj, (0,0), win32con.SRCCOPY)
        dataBitMap.SaveBitmapFile(cDC, path)

        # Free Resources
        dcObj.DeleteDC()
        cDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, wDC)
        win32gui.DeleteObject(dataBitMap.GetHandle())

    def exec_brb(self):
        self.log.info("Executing BRB macro.")
        self.last_scene = self.obs.get_current_scene_name()
        self.playpause_music()
        self.mute_microphone()
        self.obs.set_current_scene_name("BRB")

    def exec_back(self):
        self.log.info("Executing back macro")
        self.obs.set_current_scene_name(self.last_scene)
        self.playpause_music()

    def exec_post_rtb(self):
        self.capture_window("Reflector 4 - huginn", 2360, 2360, "rtb.bmp")


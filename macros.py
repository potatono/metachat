import sys
import os
import time
import shutil
import subprocess
import urllib.parse

if sys.platform == "win32":
    import voicemeeterlib
    import win32api
    from win32con import VK_MEDIA_PLAY_PAUSE, KEYEVENTF_EXTENDEDKEY

from config import *
from logs import *
from obs import ObsApp

class Macros:
    def __init__(self):
        self.log = Logger("macros")
        self.obs = ObsApp()
        self.obs.ensure_connected()
        self.clip_src_path = CONFIG.get("macros", "clip_src_path")
        self.clip_src_prefix = CONFIG.get("macros", "clip_src_prefix")
        self.clip_edit_prefix = CONFIG.get("macros", "clip_edit_prefix")
        self.clip_public_path = CONFIG.get("macros", "clip_public_path")
        self.clip_public_prefix = CONFIG.get("macros", "clip_public_prefix")
        self.clip_public_url_prefix = CONFIG.get("macros", "clip_public_url_prefix")
        
        self.context_clip = None
        self.context_time = None

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

    def exec_find_last_clip(self):
        # OBS doesn't return the path to the clip, so get the newest file with the clips prefix
        # in the clips path
        files = os.listdir(self.clip_src_path)
        files = [f for f in files if f.startswith(self.clip_src_prefix)]
        files.sort(reverse=True)
        if len(files) > 0:
            self.log.info(f"Clip saved: {files[0]}")
            return files[0]
        else:
            self.log.error("No clip saved")
            return None

    def get_clip_duration(self, path):
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            self.log.error("ffprobe not found")
            return None
        
        try:
            cmd = [
                ffprobe, 
                "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                path
            ]
            duration = float(subprocess.check_output(cmd).decode().strip())
            return duration
        except Exception as ex:
            self.log.error("While getting clip duration", ex)
            return None
    
    def exec_trim_clip(self, clip, duration=0):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.log.error("ffmpeg not found")
            return False
        
        try:
            src_path = clip['path']
            dst_filename = clip['filename'].replace(self.clip_src_prefix, self.clip_edit_prefix)
            dst_path = clip['path'].replace(self.clip_src_prefix, self.clip_edit_prefix)
            start = clip['duration'] - duration

            cmd = [
                ffmpeg, 
                "-y", 
                "-ss", str(start), 
                "-i", src_path, 
                "-c", "copy", 
                dst_path
            ]
            subprocess.check_output(cmd)

            return {
                "filename": dst_filename,
                "path": dst_path,
                "duration": duration,
                "start_time": clip['start_time'] + start,
                "end_time": clip['end_time']
            }
        
        except Exception as ex:
            self.log.error("While trimming clip", ex)
            return None

    def exec_post_clip(self, clip):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.log.error("ffmpeg not found")
            return False
        
        try:
            src_path = clip['path']
            ## TODO FIXME This breaks if you change OBS settings
            dst_filename = clip['filename'].replace(self.clip_src_prefix, self.clip_public_prefix).replace('.mkv','.mp4')
            dst_path = os.path.join(self.clip_public_path, dst_filename)

            url = f"{self.clip_public_url_prefix}/{urllib.parse.quote(dst_filename)}"

            cmd = [
                ffmpeg, 
                "-y", 
                "-i", src_path, 
                "-c", "copy", 
                dst_path
            ]
            subprocess.check_output(cmd)

            return {
                "filename": dst_filename,
                "path": dst_path,
                "duration": clip['duration'],
                "start_time": clip['start_time'],
                "end_time": clip['end_time'],
                "url": url
            }

        except Exception as ex:
            self.log.error("While trimming clip", ex)
            return None


    def exec_save_clip(self):
        self.log.info("Executing save clip macro")

        clip = { "end_time": time.time() }

        self.obs.save_clip()
        filename = self.exec_find_last_clip()

        if not filename:
            return None

        clip['filename'] = filename        
        clip['path'] = os.path.join(self.clip_src_path, clip['filename'])
        clip['duration'] = self.get_clip_duration(clip['path'])
        clip['start_time'] = clip['end_time'] - clip['duration']

        self.context_clip = clip

        return clip



        



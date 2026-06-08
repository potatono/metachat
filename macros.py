import sys
import os
import time
import shutil
import subprocess
import urllib.parse

from random import randint
import requests

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
        self.brb_playpause_enabled = CONFIG.getboolean("macros", "brb_playpause_enabled", fallback=True)
        self.webhook_url = SECRETS.get("macros", "webhook_url", fallback=None)
        
        self.context_clip = None
        self.context_time = None
        self.context_shot = None

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


    # Hey Bobby, please start a break
    def exec_brb(self):
        self.log.info("Executing BRB macro.")
        self.last_scene = self.obs.get_current_scene_name()
        if self.brb_playpause_enabled:
            self.playpause_music()
        self.mute_microphone()
        self.obs.set_current_scene_name("BRB")

    # Hey Bobby, please bring us back
    def exec_back(self):
        self.log.info("Executing back macro")
        self.obs.set_current_scene_name(self.last_scene)
        if self.brb_playpause_enabled:
            self.playpause_music()

    # Hey Bobby, please find the last clip
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
            duration = int(float(subprocess.check_output(cmd).decode().strip()))

            return duration
        
        except Exception as ex:
            self.log.error("While getting clip duration", ex)
            return None
    
    def exec_trim_clip(self, clip, word1, word2, duration=0):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.log.error("ffmpeg not found")
            return False
        
        try:
            src_path = clip['path']
            dst_filename = self.get_dest_filename(self.clip_edit_prefix, word1, word2)
            dst_path = os.path.join(self.clip_public_path, dst_filename)
            start = clip['duration'] - duration

            cmd = [
                ffmpeg, 
                "-y", 
                "-ss", str(start), 
                "-i", src_path, 
                "-c", "copy", 
                dst_path
            ]
            self.log.info(cmd)
            output = subprocess.check_output(cmd)
            self.log.info(output)

            url = f"{self.clip_public_url_prefix}/{urllib.parse.quote(dst_filename)}"

            return {
                "filename": dst_filename,
                "path": dst_path,
                "duration": duration,
                "start_time": clip['start_time'] + start,
                "end_time": clip['end_time'],
                "url": url
            }
        
        except Exception as ex:
            self.log.error("While trimming clip", ex)
            return None

    # def exec_post_clip(self, clip):
    #     ffmpeg = shutil.which("ffmpeg")
    #     if not ffmpeg:
    #         self.log.error("ffmpeg not found")
    #         return False
        
    #     try:
    #         src_path = clip['path']
    #         ## TODO FIXME This breaks if you change OBS settings
    #         dst_filename = clip['filename'].replace(self.clip_src_prefix, self.clip_public_prefix).replace('.mkv','.mp4')
    #         dst_path = os.path.join(self.clip_public_path, dst_filename)

    #         url = f"{self.clip_public_url_prefix}/{urllib.parse.quote(dst_filename)}"

    #         cmd = [
    #             ffmpeg, 
    #             "-y", 
    #             "-i", src_path, 
    #             "-c", "copy", 
    #             dst_path
    #         ]
    #         self.log.info(cmd)
    #         output = subprocess.check_output(cmd)
    #         self.log.info(output)

    #         return {
    #             "filename": dst_filename,
    #             "path": dst_path,
    #             "duration": clip['duration'],
    #             "start_time": clip['start_time'],
    #             "end_time": clip['end_time'],
    #             "url": url
    #         }

    #     except Exception as ex:
    #         self.log.error("While trimming clip", ex)
    #         return None

    def get_dest_filename(self, prefix, word1, word2, ext="mp4"):
        num = randint(100,999)
        dest_filename = f"{prefix}-{word1}-{word2}-{num}.{ext}"
        dest_path = os.path.join(self.clip_public_path, dest_filename)

        if os.path.exists(dest_path):
            return self.get_dest_clip_path(word1, word2)
        
        return dest_filename
    
    def exec_save_screenshot(self, word1, word2):
        self.log.info("Executing save screenshot macro")

        dest_filename = self.get_dest_filename("shot", word1, word2, "jpg")
        dest_path = os.path.join(self.clip_public_path, dest_filename)

        self.obs.save_screenshot(dest_path)
        
        url = f"{self.clip_public_url_prefix}/{urllib.parse.quote(dest_filename)}"

        screenshot = { 
            "filename": dest_filename,
            "path": dest_path,
            "url": url
        }

        self.context_shot = screenshot

        return screenshot

    def exec_save_clip(self, word1, word2):
        self.log.info("Executing save clip macro")

        clip = { "end_time": time.time() }

        self.obs.save_clip()
        filename = self.exec_find_last_clip()

        if not filename:
            return None

        src_path = os.path.join(self.clip_src_path, filename)
        dest_filename = self.get_dest_filename(self.clip_public_prefix, word1, word2)
        dest_path = os.path.join(self.clip_public_path, dest_filename)

        self.log.info(f"Copying clip {src_path} to {dest_path}...")
        shutil.copy(src_path, dest_path)

        url = f"{self.clip_public_url_prefix}/{urllib.parse.quote(dest_filename)}"

        clip['filename'] = dest_filename        
        clip['path'] = dest_path
        clip['duration'] = self.get_clip_duration(clip['path'])
        clip['start_time'] = clip['end_time'] - clip['duration']
        clip['url'] = url

        self.log.info(clip)

        self.context_clip = clip

        return clip

    def exec_share(self, item="clip"):
        context = None
        if item == "clip":
            context = self.context_clip
        elif item == "shot":
            context = self.context_shot

        if not context:
            self.log.error(f"Attempted to share {item} with no {item} in context.")
            return None
        
        if not self.webhook_url:
            self.log.error(f"Attempted to share {item} with no webhook url defined.")
            return None
        
        url = context.get("url")

        if not url:
            self.log.error(f"Attempted to share a {item} without a url")
            return None
        
        streamer_name = CONFIG.get("streamer","name")

        if not streamer_name:
            self.log.error(f"Attempted to share a {item} but streamer name not set")
            return None

        requests.post(self.webhook_url, json={"text":f"{streamer_name} shared this {item}: {url}"})

        return True

    def exec_announce(self):       
        if not self.webhook_url:
            self.log.error(f"Attempted to announce with no webhook url defined.")
            return None
                
        streamer_name = CONFIG.get("streamer","name")

        if not streamer_name:
            self.log.error(f"Attempted to announce but streamer name not set")
            return None

        # TODO Move to config
        msg = (f"{streamer_name} is now live.\n\n"
                "*  https://twitch.tv/potate_oh_no\n"
                "*  https://www.youtube.com/@potateohno\n"
                "*  https://www.linkedin.com/in/justin-day-04b2ba/\n"
                "*  https://www.facebook.com/potateohno\n"
                "*  https://x.com/potatono\n")

        requests.post(self.webhook_url, json={"text":msg})

        return True




        



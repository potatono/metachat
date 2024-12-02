import time
from threading import Thread

import azure.cognitiveservices.speech as speechsdk
import pygame
from pygame.locals import*
from obswebsocket import obsws, requests

from config import *
from logs import *

class AvatarApp():
    thread = None
    running = False
    queue = []
    last_completion = None
    is_talking = False
    future = None
    viseme_id = None
    viseme_changed = False

    def __init__(self):
        self.log = Logger(f"avatar")
        self.images = []
        self.voice = CONFIG.get("avatar", "voice", fallback="en-US-AvaMultilingualNeural")
        self.width = CONFIG.getint("avatar", "width", fallback=900)
        self.height = CONFIG.getint("avatar", "height", fallback=860)
        self.speech_key = SECRETS.get("avatar", "speech_key", fallback=None)
        self.speech_region = CONFIG.get("avatar", "speech_region", fallback="eastus")

        self.enable_title_updates = CONFIG.getboolean("avatar", "enable_title_updates", fallback=False)
        self.enable_obs_updates = CONFIG.getboolean("avatar", "enable_obs_updates", fallback=False)
        self.source_name = CONFIG.get("avatar", "obs_source_name", fallback=None)

        self.init_images()
        self.init_pygame()
        self.init_obs()

        self.thread = Thread(daemon=True, target=self.loop)

    def start(self):
        self.log.info(f"Starting Avatar engine")
        self.running = True
        self.thread.start()

    def shutdown(self):
        if self.enable_obs_updates:
            self.ws.disconnect()
            self.ws = None

        self.running = False
        self.thread.join()
        pygame.display.quit()

    def say(self, text):
        self.log.info(f"Appending {text}")
        self.queue.append(text)

    def init_images(self):
        for i in range(0,22):
            self.images.append(pygame.image.load(f"avatar/bobby-id-{i}.png"))
    
    def init_pygame(self):
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("metachat")
        self.clock = pygame.time.Clock()

    def init_obs(self):
        if self.enable_obs_updates:
            self.ws = obsws("localhost", 4455, SECRETS.get("avatar", "websocket_secret"))
            self.ws.connect()

    def close_pygame(self):
        pygame.display.quit()

    def update_title(self):
        if self.enable_title_updates:
            if self.is_talking:
                pygame.display.set_caption("metachat talking")
            else:
                pygame.display.set_caption("metachat")
    
    def on_viseme(self, evt):
        self.viseme_id = evt.viseme_id
        self.viseme_changed = True

    def on_completed(self, evt):
        self.log.info("TTS complete")
        self.last_completion = time.time()
        self.is_talking = False
        self.update_obs()
        self.update_title()
        self.future.get()

    def blit_visime(self):
        self.screen.blit(self.images[self.viseme_id],(0,0))
        pygame.display.flip()

    def init_tts(self):
        speech_config = speechsdk.SpeechConfig(subscription=self.speech_key,
                                               region=self.speech_region)
        audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
        speech_config.speech_synthesis_voice_name = self.voice

        self.tts = speechsdk.SpeechSynthesizer(speech_config=speech_config, 
                                               audio_config=audio_config)
        self.tts.viseme_received.connect(self.on_viseme)
        self.tts.synthesis_completed.connect(self.on_completed)

    def process_tts(self):
        if len(self.queue)>0 and not self.is_talking:                
            self.log.info("Starting TTS")
            msg = self.queue.pop(0)
            self.log.info(f"Saying {msg}")
            self.is_talking = True
            self.update_obs()
            self.update_title()
            self.future = self.tts.speak_text_async(msg)

    def update_viseme(self):
        if self.viseme_changed:
            self.blit_visime()
            self.viseme_changed = False

    def update_obs(self):
        if self.enable_obs_updates:
            self.toggle_obs(self.is_talking)

    def toggle_obs(self, toggle_to=True):
        scene = self.ws.call(requests.GetCurrentProgramScene())
        uuid = scene.getSceneUuid()
        self.log.debug(f"Current scene uuid is {uuid}")
    
        items = self.ws.call(requests.GetSceneItemList(sceneUuid=uuid))
        for i in items.getSceneItems():
            if i['sourceName'] == self.source_name:
                itemId = i['sceneItemId']
                self.toggle_scene_item(uuid, itemId, toggle_to)
        
    def toggle_scene_item(self, uuid, itemId, toggle_to=True):
        self.ws.call(requests.SetSceneItemEnabled(sceneUuid=uuid, 
                                                  sceneItemId=itemId, 
                                                  sceneItemEnabled=toggle_to))

    def loop(self):
        self.init_tts()

        while self.running:
            self.process_tts()
                
            self.update_viseme()

            self.clock.tick(60)

if __name__ == "__main__":
    tts = AvatarApp()
    tts.start()
    tts.say("i'm not just any bot, i'm bobbychatbot!")
    tts.say("i add a sprinkle of sarcasm and unsolicited advice to spice things up around here.")
    tts.say("sometimes i even tell jokes. you know, just living my best virtual life.")
    time.sleep(10)
    while tts.is_talking:
        print("Sleeping")
        time.sleep(1)

    tts.shutdown()
    print("Done")



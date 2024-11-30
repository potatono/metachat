import time
from threading import Thread

import azure.cognitiveservices.speech as speechsdk
import pygame
from pygame.locals import*

from config import *
from logs import *

class AvatarApp():
    thread = None
    running = False
    queue = []
    last_completion = None

    def __init__(self):
        self.log = Logger(f"avatar")
        self.images = []
        self.voice = CONFIG.get("avatar", "voice", fallback="en-US-AvaMultilingualNeural")
        self.width = CONFIG.getint("avatar", "width", fallback=900)
        self.height = CONFIG.getint("avatar", "height", fallback=860)
        self.speech_key = SECRETS.get("avatar", "speech_key", fallback=None)
        self.speech_region = CONFIG.get("avatar", "speech_region", fallback="eastus")

        self.init_images()
        #self.init_pygame()

        self.thread = Thread(daemon=True, target=self.loop)

    def start(self):
        self.log.info(f"Starting Avatar engine")
        self.running = True
        self.thread.start()

    def say(self, text):
        self.log.info(f"Appending {text}")
        self.queue.append(text)

    def init_images(self):
        for i in range(0,22):
            self.images.append(pygame.image.load(f"viseme/viseme-id-{i}.jpg"))
    
    def init_pygame(self):
        self.screen = pygame.display.set_mode((self.width, self.height))
    
    def close_pygame(self):
        pygame.display.quit()

    def on_viseme(self, evt):
        self.screen.blit(self.images[evt.viseme_id],(0,0))
        pygame.display.flip()

    def loop(self):
        #self.init_pygame()

        speech_config = speechsdk.SpeechConfig(subscription=self.speech_key,
                                               region=self.speech_region)
        audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
        speech_config.speech_synthesis_voice_name = self.voice

        self.tts = speechsdk.SpeechSynthesizer(speech_config=speech_config, 
                                               audio_config=audio_config)
        self.tts.viseme_received.connect(self.on_viseme)
        
        while True:
            if len(self.queue)>0:
                self.init_pygame()

                while len(self.queue)>0:
                    self.log.info("Starting TTS")
                    msg = self.queue.pop(0)
                    self.log.info(f"Saying {msg}")
                    self.tts.speak_text_async(msg).get()
                    self.log.info("TTS complete")
                    self.last_completion = time.time()
                
                self.close_pygame()
            
            time.sleep(0.25)

if __name__ == "__main__":
    tts = AvatarApp()
    tts.start()
    tts.say("The quick brown fox jumps over the lazy yellow dog.")
    tts.say("But what the fox say?")
    time.sleep(30)
    tts.say("The fox say dooooooooooooooooooom")
    print("Sleeping")
    time.sleep(10)

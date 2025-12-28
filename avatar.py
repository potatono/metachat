import time
import json
import re
from random import random
from threading import Thread

import azure.cognitiveservices.speech as speechsdk
import pygame
from pygame.locals import *

from config import *
from logs import *
from obs import ObsApp

class AvatarApp():
    #thread = None
    running = False
    queue = []
    last_completion = None
    is_talking = False
    future = None
    viseme_id = 0
    viseme_changed = False
    left_eye_id = 0
    right_eye_id = 0
    is_ack = False
    angle = 0.0
    scale = 1.0

    def __init__(self):
        self.log = Logger(f"avatar")
        self.images = []
        self.mouths = []
        self.eyes = []
        self.body = None

        self.voice = CONFIG.get("avatar", "voice", fallback="en-US-AvaMultilingualNeural")
        self.width = CONFIG.getint("avatar", "width", fallback=900)
        self.height = CONFIG.getint("avatar", "height", fallback=860)
        self.speech_key = SECRETS.get("avatar", "speech_key", fallback=None)
        self.speech_region = CONFIG.get("avatar", "speech_region", fallback="eastus")

        self.enable_title_updates = CONFIG.getboolean("avatar", "enable_title_updates", fallback=False)
        self.enable_obs_updates = CONFIG.getboolean("avatar", "enable_obs_updates", fallback=False)
        self.source_name = CONFIG.get("avatar", "obs_source_name", fallback=None)

        self.background_color = CONFIG.get("avatar", "background_color", fallback="d7833a")
        self.mouth_position = (
            CONFIG.getint("avatar", "mouth_position_x", fallback=0), 
            CONFIG.getint("avatar", "mouth_position_y", fallback=0)        
        )

        self.left_eye_position = (
            CONFIG.getint("avatar", "left_eye_position_x", fallback=0), 
            CONFIG.getint("avatar", "left_eye_position_y", fallback=0)
        
        )
        self.right_eye_position = (
            CONFIG.getint("avatar", "right_eye_position_x", fallback=0), 
            CONFIG.getint("avatar", "right_eye_position_y", fallback=0)
        
        )

        self.emoji_map = json.loads(CONFIG.get("avatar", "emoji_emotion_map", fallback="{}"))
        self.emotion_map = json.loads(CONFIG.get("avatar", "emotion_eye_map", fallback="{}"))

        self.init_images()
        self.init_pygame()
        self.init_obs()
        self.init_corrections()

    def start(self):
        self.log.info(f"Starting Avatar engine")
        self.running = True

        self.init_tts()
        self.blit_viseme()
        self.update_obs()

    def shutdown(self):
        if self.enable_obs_updates:
            self.obs.shutdown()

        self.running = False
        pygame.display.quit()

    def say(self, text):
        self.log.info(f"Appending {text}")
        self.is_ack = False

        self.queue.append(text)

    def ack(self):
        if self.is_ack:
            return
        
        self.is_ack = True
        self.left_eye_id = 6
        self.right_eye_id = 6
        self.viseme_id = 21
        self.is_talking = True
        self.viseme_changed = True
        self.update_obs()
        self.update_title()
        self.is_talking = False
        self.ack_sound.play()

    def init_images(self):
        for i in range(0,22):
            self.images.append(pygame.image.load(f"avatar/bobby-id-{i}.png"))
            self.mouths.append(pygame.image.load(f"avatar/mouth-id-{i}.png"))
        
        for i in range(0,10):
            self.eyes.append(pygame.image.load(f"avatar/eye-id-{i}.png"))

        self.body = pygame.image.load("avatar/bobby-body.png")
    
    def init_pygame(self):
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("metachat")
        self.clock = pygame.time.Clock()
        pygame.mixer.init()
        self.ack_sound = pygame.mixer.Sound("avatar/ack.wav")
        self.temp_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

    def init_obs(self):
        if self.enable_obs_updates:
            self.obs = ObsApp()

    def init_corrections(self):
        self.corrections = []

        xlat = CONFIG.get("avatar", "corrections")
        parts = re.split("\\s*,\\s*", xlat)

        for part in parts:
            self.corrections.append(part.split(':'))

    def apply_corrections(self, text):
        for (bad,good) in self.corrections:
            text = re.sub(f"\\b{bad}\\b", f"{good}", text, re.IGNORECASE)
        
        return text

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

        if self.viseme_id == 0:
            self.angle = random() * 20 - 10
            self.scale = 1.0 + (random() * 0.5 - 0.25)

        self.viseme_changed = True

    def on_completed(self, evt):
        self.log.info("TTS complete")
        self.last_completion = time.time()
        self.is_talking = False
        self.scale = 1.0
        self.angle = 0.0
        self.update_obs()
        self.update_title()
        self.future.get()

    def blit_viseme(self):
        #self.screen.blit(self.images[self.viseme_id],(0,0))

        # Set background color
        self.screen.fill(pygame.Color('#'+self.background_color))

        # Clear the temp_surface with transparent pixels
        self.temp_surface.fill((0,0,0,0))
        
        # Draw the body
        self.temp_surface.blit(self.body,(0,0))

        # Draw the mouth
        self.temp_surface.blit(self.mouths[self.viseme_id],self.mouth_position)

        # Draw the eyes
        self.temp_surface.blit(self.eyes[self.left_eye_id],self.left_eye_position)
        self.temp_surface.blit(self.eyes[self.right_eye_id],self.right_eye_position)

        # Rotate and scale
        rotated = pygame.transform.rotozoom(self.temp_surface, self.angle, self.scale)
        rotated_rect = rotated.get_rect(center=(512, 512))

        # Update the display
        self.screen.blit(rotated,rotated_rect)
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

    def process_emoji(self, text):
        self.left_eye_id = 0
        self.right_eye_id = 0
        
        for emoji,emotion in self.emoji_map.items():
            if emoji in text:
                text = re.sub(emoji, "", text)
                self.log.debug(f"Emoji {emoji} found, setting emotion to {emotion}.")
                self.left_eye_id = self.emotion_map[emotion]['left']
                self.right_eye_id = self.emotion_map[emotion]['right']
                self.viseme_changed = True
                break

        return text

    def process_ack(self):
        if self.is_ack:
            self.left_eye_id -= 1
            if self.left_eye_id < 0:
                self.left_eye_id = 9

            self.right_eye_id += 1
            if self.right_eye_id > 9:
                self.right_eye_id = 0
            
            self.viseme_changed = True

    def process_tts(self):
        if len(self.queue)>0 and not self.is_talking:                
            self.log.info("Starting TTS")
            msg = self.queue.pop(0)
            msg = self.process_emoji(msg)
            msg = self.apply_corrections(msg)
            self.log.info(f"Saying {msg}")
            self.is_talking = True
            self.update_obs()
            self.update_title()
            self.future = self.tts.speak_text_async(msg)

    def update_viseme(self):
        if self.viseme_changed:
            self.blit_viseme()
            self.viseme_changed = False

    def update_obs(self):
        if self.enable_obs_updates:
            self.obs.ensure_connected()
            self.toggle_obs(self.is_talking)

    def toggle_obs(self, toggle_to=True):
        uuid = self.obs.get_current_scene_uuid()
        item_id = self.obs.get_scene_item_by_name(uuid, self.source_name)
        self.obs.set_scene_item_enabled(uuid, item_id, toggle_to)

    def tick(self):
        if self.running:
            pygame.event.pump()

            self.process_ack()
            self.process_tts()
                
            self.update_viseme()


    def loop(self, stop_after=None):
        start_time = time.time()
        while self.running:
            self.tick()

            if stop_after:
                time_elapsed = time.time() - start_time
                self.running = time_elapsed < stop_after

            self.clock.tick(60)

if __name__ == "__main__":
    tts = AvatarApp()
    tts.start()
    tts.ack()
    tts.loop(stop_after=3)
    tts.running = True
    tts.say("😥 i'm not just any bot, i'm bobbychatbot!")
    tts.say("i add a sprinkle of sarcasm and unsolicited advice 🤔 to spice things up around here.")
    tts.say("sometimes i even tell jokes. 😂")
    tts.say("you know, just living my best virtual life.")
    tts.say("but don't spam me, or i'll have to send you to the digital timeout corner. 🤬")
    tts.loop(stop_after=20)

    tts.shutdown()
    print("Done")



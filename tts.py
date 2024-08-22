import time
from threading import Thread

import pyttsx3

from config import *
from logs import *

''' TTS App for talking '''
class TTSApp():
    thread = None
    running = False
    queue = []

    def __init__(self):
        self.log = Logger(f"TTS")
        
        self.thread = Thread(daemon=True, target=self.loop)

    def start(self):
        self.log.info(f"Starting TTS engine")
        self.running = True
        self.thread.start()

    def say(self, text):
        self.log.info(f"Appending {text}")
        self.queue.append(text)

    def loop(self):
        self.tts = pyttsx3.init()

        voice_idx = CONFIG.getint("tts", "voice", fallback=0)
        voices = self.tts.getProperty('voices')
        self.tts.setProperty('voice', voices[voice_idx].id)

        while True:
            while len(self.queue)>0:
                self.log.info("Starting TTS")
                msg = self.queue.pop(0)
                self.log.info(f"Saying {msg}")
                self.tts.say(msg)
                self.tts.runAndWait()
                self.log.info("TTS complete")
            
            time.sleep(0.25)

if __name__ == "__main__":
    tts = TTSApp()
    tts.start()
    tts.say("The quick brown fox jumps over the lazy yellow dog.")
    tts.say("But what the fox say?")
    tts.say("The fox say dooooooooooooooooooom")
    print("Sleeping")
    time.sleep(10)

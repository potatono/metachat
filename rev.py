import queue
import json
import re
from threading import Thread, Timer

import pyaudio
from rev_ai.models import MediaConfig
from rev_ai.streamingclient import RevAiStreamingClient

from config import *
from logs import *

class MicrophoneStream(object):
    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk
        self._buff = queue.Queue()
        self.closed = True

    def __enter__(self):
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            channels=1, rate=self._rate,
            input=True, frames_per_buffer=self._chunk,
            
            #Run the audio stream asynchronously to fill the buffer object.
            #This is necessary so that the input device's buffer doesn't
            #overflow while the calling thread makes network requests, etc.
            
            stream_callback=self._fill_buffer,
        )

        self.closed = False

        return self

    def __exit__(self, type, value, traceback):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        """
        Signal the generator to terminate so that the client's
        streaming_recognize method will not block the process termination.
        """
        self._buff.put(None)
        self._audio_interface.terminate()

    def _fill_buffer(self, in_data, frame_count, time_info, status_flags):
        """
        Continuously collect data from the audio stream, into the buffer.
        """
        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def generator(self):
        while not self.closed:
            """
            Use a blocking get() to ensure there's at least one chunk of
            data, and stop iteration if the chunk is None, indicating the
            end of the audio stream.
            """
            chunk = self._buff.get()
            if chunk is None:
                return
            data = [chunk]

            """
            Now consume whatever other data's still buffered.
            """
            while True:
                try:
                    chunk = self._buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b''.join(data)

class TranscriptApp():
    def __init__(self, on_text):
        self.log = Logger("rev")
        self.on_text_cb = on_text
        self.mc = MediaConfig('audio/x-raw', 'interleaved', 44100, 'S16LE', 1)
        self.token = SECRETS.get("rev.ai", "token")
        self.streamclient = RevAiStreamingClient(self.token, self.mc)
        self.thread = Thread(daemon=True, target=self.loop)
        self.running = False
        self.text = None
        self.sendTimer = None
        self.init_corrections()

    def start(self):
        self.log.info("Starting audio transcript thread...")
        self.running = True
        self.thread.start()

    def loop(self):
        rate = 44100
        chunk = int(rate/10)

        self.log.info("Opening microphone input...")

        with MicrophoneStream(rate, chunk) as stream:
            try:
                response_gen = self.streamclient.start(stream.generator())

                for response in response_gen:
                    self.handle_response(response)                
            except KeyboardInterrupt:
                self.streamclient.end()
                pass

        self.log.info("Microphone loop finished..")

    def init_corrections(self):
        self.corrections = []

        xlat = CONFIG.get("rev.ai", "corrections")
        parts = re.split("\\s*,\\s*", xlat)

        for part in parts:
            self.corrections.append(part.split(':'))

    def apply_corrections(self, text):
        for (bad,good) in self.corrections:
            text = re.sub(f"\\b{bad}\\b", f"{good}", text, re.IGNORECASE)
        
        return text

    def handle_response(self, response):
        data = json.loads(response)
            
        if data["type"] == "final":
            v = [i['value'] for i in data['elements']]
            text = ''.join(v)

            text = self.apply_corrections(text)
            
            #self.on_text_cb(text)
            self.append_text(text)

    def append_text(self, text):
        if self.text is None:
            self.text = text
        else:
            self.text = ' '.join([self.text, text])

        self.log.info("Text buffer is '%s'", self.text)

        if self.sendTimer is not None:
            self.sendTimer.cancel()

        self.sendTimer = Timer(CONFIG.getfloat("rev.ai", "send_timeout", fallback=1.0), self.send_text)
        self.sendTimer.start()

    def send_text(self):
        self.log.info("Sending text '%s'", self.text)
        self.on_text_cb(self.text)
        self.sendTimer = None
        self.text = None

    def shutdown(self):
        if not self.running:
            print("Refusing to shut down.  Not started.")
            return
            
        self.log.info("Shutting down..")
        self.running = False
        self.streamclient.end()
        self.thread.join()


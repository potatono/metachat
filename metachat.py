
import curses
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from time import sleep
from queue import Queue

from chat import ChatApp
from streamer import StreamerApp
from chatbot import ChatbotApp
from reactions import ReactionsApp

from config import CONFIG
from logs import Logger
from eventbus import eventbus, Events

application = None

''' Main application with integrated curses interface '''
class Application():
    token = None
    running = False
    history = []

    def __init__(self):
        # Note: Logger creation is deferred until after curses init
        self.chat = None
        self.streamer = None
        self.chatbot = None
        self.reactions = None
        self.queue = Queue()
        
        # Curses state
        self.stdscr = None
        self.log_window = None
        self.input_window = None
        self.log_lines = []
        self.max_log_lines = 1000
        self.current_line = ""
        self.prompt = "Streamer> "

        self.streamer_name = CONFIG.get("streamer", "name")

    def init_components(self):
        """Initialize components after curses is set up"""
        self.log = Logger("metachat")
        self.chat = ChatApp()        
        self.streamer = StreamerApp()
        self.chatbot = ChatbotApp()
        self.reactions = ReactionsApp()

    def start(self):
        """Start the application with curses wrapper"""
        self.running = True
        curses.wrapper(self.curses_main)

    def curses_main(self, stdscr):
        """Main function called by curses.wrapper"""
        self.stdscr = stdscr
        self.init_curses()
        self.init_components()  # Initialize components after curses
        
        self.log.info(f"Application started on {datetime.now().strftime('%c')}..")  
        self.loop()

    def init_curses(self):
        """Initialize curses windows and settings"""
        curses.curs_set(1)  # Show cursor
        curses.noecho()     # Don't echo input automatically
        
        height, width = self.stdscr.getmaxyx()
        
        # Log window (top area, leave 3 lines for input)
        log_height = height - 3
        self.log_window = curses.newwin(log_height, width, 0, 0)
        self.log_window.scrollok(True)
        
        # Input window (bottom area)
        self.input_window = curses.newwin(2, width, log_height, 0)
        
        # Draw separator line
        self.stdscr.hline(log_height, 0, curses.ACS_HLINE, width)
        
        # Non-blocking input
        self.stdscr.nodelay(True)
        self.stdscr.timeout(0)  # Immediate return if no input
        
        self.redraw_input()

    def ensure_connected(self):
        if self.streamer:
            self.streamer.ensure_connected()
        if self.chat:
            self.chat.ensure_connected()
        if self.chatbot:
            self.chatbot.ensure_connected()
        if self.reactions:
            self.reactions.ensure_connected()

    def shutdown(self):
        if self.chat:
            self.chat.shutdown()
        if self.streamer:
            self.streamer.shutdown()
        if self.chatbot:
            self.chatbot.shutdown()
        if self.reactions:
            self.reactions.shutdown()

    def tick(self):
        if self.chatbot:
            self.chatbot.tick()
    
    def loop(self):
        framecount = 0
        while self.running:
            try:
                framecount += 1

                if framecount % 180 == 0:
                    self.ensure_connected()

                # Handle curses input and display
                self.handle_curses()

                self.tick()

                sleep(1/60.0)
            except KeyboardInterrupt:
                break
            except Exception as ex:
                if self.log:
                    self.log.error("Exception in metachat", exc_info=ex)

        if self.log:
            self.log.info("Application loop finished..")
        self.shutdown()

    def handle_curses(self):
        """Handle curses input and log display"""
        # Process queued log messages
        self.process_log_messages()
        
        # Handle keyboard input
        try:
            key = self.stdscr.getch()
            if key != curses.ERR:
                self.handle_key(key)
        except curses.error:
            pass

    def handle_key(self, key):
        """Handle individual keypress"""
        if key == 3 or key == 27:  # Ctrl+C or ESC
            self.running = False
        elif key == 10 or key == 13:  # Enter
            if self.current_line.strip():
                # Add input to log display
                self.add_log_line(f"Input: {self.current_line}")
                # Publish event
                eventbus.publish(Events.STREAMER_LINE, self.current_line.strip(), "keyboard")
                eventbus.publish(Events.STREAMER_PHRASE, self.current_line.strip(), "keyboard")
                self.current_line = ""
                self.redraw_input()
        # elif key == 32:  # Space
        #     if self.current_line.strip():
        #         # Route words to on_voice
        #         #self.on_voice(self.current_line.strip())
        #         last_word = self.current_line.strip().split()[-1]
        #         eventbus.publish(Events.STREAMER_PHRASE, last_word, "keyboard")
        #     self.current_line += chr(key)
        #     self.redraw_input()
        elif key in (127, 8, curses.KEY_BACKSPACE):  # Backspace
            if self.current_line:
                self.current_line = self.current_line[:-1]
                self.redraw_input()
        elif 32 <= key <= 126:  # Printable characters
            self.current_line += chr(key)
            self.redraw_input()

    def process_log_messages(self):
        """Process queued log messages from other threads"""
        from logs import CursesLogHandler
        message_queue = CursesLogHandler.get_message_queue()
        updated = False
        
        try:
            while True:
                message = message_queue.get_nowait()
                self.add_log_line(message)
                updated = True
        except:
            pass
            
        if updated:
            self.redraw_logs()

    def add_log_line(self, line):
        """Add a line to the log buffer, splitting multi-line messages"""
        # Split on newlines to handle tracebacks properly
        lines = line.split('\n')
        for single_line in lines:
            self.log_lines.append(single_line)
            if len(self.log_lines) > self.max_log_lines:
                self.log_lines.pop(0)

    def redraw_logs(self):
        """Redraw the log window"""
        try:
            self.log_window.clear()
            height, width = self.log_window.getmaxyx()
            
            # Show recent lines that fit in the window
            start_idx = max(0, len(self.log_lines) - (height - 1))
            for i, line in enumerate(self.log_lines[start_idx:]):
                if i < height - 1:
                    display_line = line[:width-1] if len(line) >= width else line
                    try:
                        self.log_window.addstr(i, 0, display_line)
                    except curses.error:
                        pass
                        
            self.log_window.refresh()
        except curses.error:
            pass

    def redraw_input(self):
        """Redraw the input line"""
        try:
            self.input_window.clear()
            prompt_line = f"{self.prompt}{self.current_line}"
            self.input_window.addstr(0, 0, prompt_line)
            self.input_window.refresh()
        except curses.error:
            pass

if __name__ == "__main__":    
    application = Application()
    application.start()    

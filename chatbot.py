from random import random
import re
import time
import number_parser

from chatgpt import CompletionApp
from twitch import TwitchApp
from oauth import OAuthApp
from tts import TTSApp

from config import *
from logs import *

class ChatbotApp():
    token = None

    def __init__(self, on_say=None):
        self.log = Logger("chatbot")
        self.history = []
        self.on_say = on_say

        self.name = CONFIG.get("chatbot", "name")
        self.streamer_name = CONFIG.get("streamer", "name")
        self.history_size = CONFIG.getint("chatbot", "history_size", fallback=30)
        self.nicknames = CONFIG.get("chatbot", "nicknames")

        self.chatgpt = CompletionApp()

        if CONFIG.getboolean("chatbot", "send_to_twitch", fallback=True):
            self.oauth = OAuthApp(CONFIG.get("chatbot", "twitch_oauth_section"))
            self.twitch = TwitchApp(self.name, self.streamer_name)
        else:
            self.oauth = None
            self.twitch = None
        
        if CONFIG.getboolean("chatbot", "send_to_tts", fallback=False):
            self.tts = TTSApp()
        else:
            self.tts = None


    def ensure_connected(self):
        if self.twitch:
            if not self.twitch.running:
                if self.token is None:
                    self.log.info("Waiting for Chatbot Twitch token...")
                    self.token = self.oauth.get_token()
                else:
                    self.log.info("Token received.  Starting Twitch Server..")
                    self.twitch.start(self.token)        
        
        if self.tts and not self.tts.running:
            self.log.info("Starting TTS Server..")
            self.tts.start()

    def shutdown(self):
        if self.twitch:
            self.twitch.shutdown()
            self.oauth.shutdown()

    def on_message(self, message):
        self.log.info(f"Got message {message['author']}: {message['text']}")

        if self.is_command(message):
            self.process_command(message)
            return

        self.history.append(message)

        while len(self.history) >= self.history_size:
            self.history.pop(0)

        if self.should_reply(message):
            self.reply()
    
    def is_command(self, message):
        pattern = f"\\bHey (?:{self.nicknames}),? please (.+)\\b"

        if message['author'] != CONFIG.get("streamer", "name", fallback=None):
            self.log.error(f"Ignoring command from {message['author']}")
            return False

        result = re.search(pattern, message['text'], re.IGNORECASE) is not None

        self.log.info(f"is_command={result}")

        return result
    
    def process_command(self, message):
        pattern = f"\\bHey (?:{self.nicknames}),? please (.+)\\b"
        match = re.search(pattern, message['text'], re.IGNORECASE)
        cmd = match.group(1)

        self.log.info(f"processing command '{cmd}'")

        if re.search("ignore", cmd, re.IGNORECASE) is not None:
            self.process_ignore(cmd)
        elif re.search("(?:change|set) the game to", cmd, re.IGNORECASE) is not None:
            self.process_game_change(cmd)
        else:
            self.log.error(f"Couldn't parse command: '{cmd}'")

    def process_ignore(self, cmd):
        num = number_parser.parse(cmd)
        num = re.sub("\D","",num)

        if len(num) > 0:
            self.log.info(f"Setting ignore timer for {num} minutes.")
            num = int(num)
            self.say(f"[cmd] Okay I will ignore you for {num} minutes.")
            # TODO Actually do the thing
        else:
            self.log.error(f"Cannot ignore empty")
            self.say(f"[cmd] I cannot ignore for zero minutes.")
    
    def process_game_change(self, cmd):
        game = re.sub(".*(?:change|set) the game to","",cmd)
        self.log.info(game)
        game = re.sub("[^\\w\\s]", "", game)
        self.log.info(game)

        if len(game) > 0:
            self.log.info(f"Changing game to '{game}'")
            self.chatgpt.game = game
            CONFIG.set("game", "name", game)
            self.say(f"[cmd] Okay I set the game to {game}.")
            # TODO Save config
        else:
            self.log.error(f"Cannot change game to empty")
            self.say(f"[cmd] I can't set the game to empty.")

    def is_talking_to_me(self, message):
        pattern = f"\\b({self.nicknames})\\b"

        result = re.search(pattern, message['text'], re.IGNORECASE) is not None
    
        self.log.debug(f"is_talking_to_me={result}")

        return result

    def time_since_last_message(self):
        t = time.time()

        for line in reversed(self.history):
            if line['author'] == self.name:
                self.log.debug(f"time_since_last_message={t - line['sent']}")
                return t - line['sent']
        
        self.log.debug(f"time_since_last_message={t}")
        return t
    
    def just_spoke(self, message):
        result = message['author'] == self.name

        self.log.debug(f"just_spoke={result}")

        return result

    def is_a_question(self, message):
        interrogative_pattern = '\\b(?:can you|what|who|where|how|why|do you|is it the|is it this|are you|are they)\\b'
        punctuation_pattern = '\\?'
        
        result = (
            re.search(interrogative_pattern, message['text'], re.IGNORECASE) is not None or
            re.search(punctuation_pattern, message['text']) is not None
        )

        self.log.debug(f"is_a_question={result}")

        return result
        
    def is_randomly(self, val):
        r = random()
        result = r < val

        self.log.debug(f"Random {r}<{val} = {result}")
        return result
        
    def is_in_conversation(self, message):
        result = (
            (
                self.is_a_question(message) or
                self.is_randomly(0.005)
            ) 
            
            and self.time_since_last_message() < 100
        )

        self.log.debug(f"is_in_conversation={result}")
        return result

    def should_reply(self, message):
        result = (
            self.is_talking_to_me(message) or
            self.time_since_last_message() > 300 or
            self.is_in_conversation(message)
        ) and (
            not self.just_spoke(message) or
            self.is_randomly(0.1)
        )

        self.log.debug(f"should_reply={result}")
        return result

    def reply(self):
        self.log.info("Getting response")
        response = self.chatgpt.get_response(self.history)

        ## TODO Check if response is dumb or not

        self.log.info(f"Responding with {response}")
        self.say(response)

    def say(self, message):
        # If we're sending to twitch, we don't need to call on_say since the
        # message will come back through restream
        if self.twitch:
            self.twitch.say(message)
        elif self.on_say:
            msg = {
                "author": self.name,
                "text": message,
                "sent": time.time()
            }
            self.on_say(msg)
        
        if self.tts:
            self.tts.say(message)

def run_tests():
    data = {
        'bot_name': "testbot",
        'streamer_name': "testttv",
        'game': 'Test'
    }

    chatbot = ChatbotApp(data)
    chatbot.name = "testbot"

    t = time.time()
    chatbot.nicknames = "bot|robot|chat|bob|bobby"
    chatbot.history = [
        { "author": "testttv", "text": "Hello bot", "sent": t-30 },
        { "author": "testbot", "text": "Hi there!", "sent": t-15 },
        { "author": "ttesttv", "text": "What do you say?", "sent": t }
    ]

    lines = [ 
        ("Okay bot, what do we need to do here?", True),
        ("Oh this is cool.  I like that", False),
        ("Bobby you there?", True),
        ("It's on the bottom.", False)
    ]

    for (str, val) in lines:
        assert chatbot.is_talking_to_me({ "author": "testtv", "text": str }) == val, str

    lines = [
        ("What do you think", True),
        ("I think this is the one", False),
        ("Is it this one", True),
        ("The heck?", True),
        ("The heck.", False)
    ]
    for (str, val) in lines:
        assert chatbot.is_a_question({ "author": "testtv", "text": str }) == val, str

    assert chatbot.just_spoke({ "author": "testbot" }), "Was just from me"
    assert chatbot.just_spoke({ "author": "testttv" }) == False, "Wast not just from me"

    assert chatbot.time_since_last_message() >= 15, "Last message at least 15 seconds ago"
    assert chatbot.time_since_last_message() <= 16, "Last message wasn't more than 16 seconds ago"

def run_interactive():
    print("Starting interactive mode.  Chat with the bot at the prompt.")

    chatbot = ChatbotApp()
    chatbot.tts.start()

    class MockTwitch:
        def say(self, message):
            chatbot.history.append({ "author": chatbot.name, "text": message, "sent": time.time() })
            print(f"{chatbot.name}: {message}")

    chatbot.twitch = MockTwitch()

    try:
        while True:
            line = input("> ")
            chatbot.on_message({ "author": chatbot.streamer_name, "text": line })
            
    except EOFError:
        return

if __name__ == "__main__":
    #run_tests()
    run_interactive()

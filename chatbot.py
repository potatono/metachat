from random import random, randint
import os
import re
import time
import number_parser
import json
import traceback

import wordlist

from chatgpt import CompletionApp
from twitch import TwitchApp
from oauth import OAuthApp
from tts import TTSApp
from avatar import AvatarApp

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
        self.history_size = CONFIG.getint("chatbot", "history_size", fallback=100)
        self.history_path = CONFIG.get("chatbot", "history_path", fallback=None)
        self.nicknames = CONFIG.get("chatbot", "nicknames")
        self.reply_mode = CONFIG.get("chatbot", "reply_mode", fallback="conversation")
        self.bang_pattern = CONFIG.get("bangs", "pattern", fallback=None)

        self.last_message_time = 0
        self.last_interaction_time = 0

        self.load_history()
        self.chatgpt = CompletionApp()

        if CONFIG.getboolean("chatbot", "send_to_twitch", fallback=True):
            self.oauth = OAuthApp(CONFIG.get("chatbot", "twitch_oauth_section"))
            self.twitch = TwitchApp(self.name, self.streamer_name)
        else:
            self.oauth = None
            self.twitch = None
        
        if CONFIG.getboolean("chatbot", "send_to_tts", fallback=False):
            self.tts = TTSApp()
        elif CONFIG.getboolean("chatbot", "send_to_avatar", fallback=False):
            self.tts = AvatarApp()
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

    def load_history(self):
        if self.history_path and os.path.exists(self.history_path):
            with open(self.history_path, "r") as fil:
                history_data = fil.read()
                self.history = json.loads(history_data)

    def save_history(self):
        if self.history_path:
            with open(self.history_path, "w") as fil:
                history_data = json.dumps(self.history)
                fil.write(history_data)

    def append_to_history(self, message):
        self.history.append(message)

        while len(self.history) >= self.history_size:
            self.history.pop(0)
        
        self.save_history()

    def on_message(self, message):
        try:
            self.log.info(f"Got message {message['author']}: {message['text']}")

            # Make sure the message is valid
            if message['text'] is None:
                self.log.warning(f"Got unexpected empty message.  Returning early.")
                return

            if self.is_bang_command(message):
                self.process_bang_command(message)
                return
            
            # Process as command if needed        
            if self.is_voice_command(message):
                self.process_voice_command(message)
                return

            # Record last message/interaction times
            if self.is_from_me(message):
                self.last_message_time = time.time()
            
            if not self.is_from_streamer(message):
                self.last_interaction_time = time.time()

            # Add to our history log
            self.append_to_history(message)

            # Send a reply if needed
            reply_context = self.should_reply(message)
            if reply_context:
                self.reply(reply_context)
    
        except Exception as e:
            self.log.error("Caught exception in chatbot.on_message")
            self.log.error(traceback.format_exc())
    
    def is_from_streamer(self, message):
        return message['author'] == self.streamer_name
    
    def is_from_me(self, message):
        return message['author'] == self.name

    def is_bang_command(self, message):
        if not self.bang_pattern:
            self.log.error("No bang pattern defined.  Cannot process !commands")
            return False
        
        result = re.search(self.bang_pattern, message['text'], re.IGNORECASE) is not None

        return result

    def process_bang_command(self, message):
        match = re.search(self.bang_pattern, message['text'], re.IGNORECASE)
        cmd = match.group(1)

        response = CONFIG.get("bangs", f"bang_{cmd}", fallback=None)

        if response is None:
            self.log.error(f"Missing bang response for {cmd}")
            return
        
        response = re.sub("[\\r\\n]+"," ",response)
        response = json.loads(response)
            
        self.log.info(f"Replying to !{cmd} with {response}")

        self.say(response)

    def is_voice_command(self, message):
        pattern = f"\\bHey,? (?:{self.nicknames}),? please (.+)\\b"

        if not self.is_from_streamer(message):
            self.log.error(f"Ignoring command from {message['author']}")
            return False

        result = re.search(pattern, message['text'], re.IGNORECASE) is not None

        return result
    
    def process_voice_command(self, message):
        pattern = f"\\bHey,? (?:{self.nicknames}),? please (.+)\\b"
        match = re.search(pattern, message['text'], re.IGNORECASE)
        cmd = match.group(1)

        self.log.info(f"Processing voice command '{cmd}'")

        if re.search("ignore", cmd, re.IGNORECASE) is not None:
            self.process_ignore(cmd)
        elif re.search("(?:change|set) the game to", cmd, re.IGNORECASE) is not None:
            self.process_game_change(cmd)
        elif re.search("pin a message", cmd, re.IGNORECASE) is not None:
            self.process_pin_message(cmd)
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

    def process_pin_message(self, cmd):
        msg = re.sub(".*pin a message", "", cmd, re.IGNORECASE)
        self.say(f"/pin {msg}")

    def is_talking_to_me(self, message):
        if self.is_from_me(message):
            return False
        
        pattern = f"\\b({self.nicknames}|you|we|your|our)\\b"

        result = re.search(pattern, message['text'], re.IGNORECASE) is not None
    
        self.log.debug(f"is_talking_to_me={result}")

        return result

    def time_since_last_message(self):
        t = time.time() - self.last_message_time
        self.log.debug(f"time_since_last_message={t}")

        return t

    def time_since_last_message_or_tts(self):
        last_time = self.last_message_time

        if self.tts and self.tts.last_completion:
            last_time = max(last_time, self.tts.last_completion)
        
        t = time.time() - last_time
        self.log.debug(f"time_since_last_message_or_tts={t}")

        return t
    
    # Returns time since last message from someone other than the streamer    
    def time_since_last_interaction(self):

        if self.last_interaction_time == 0:
            return 0
        else:
            t = time.time() - self.last_interaction_time
            self.log.debug(f"time_since_last_interaction={t}")
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

    def is_activated(self, message):
        if self.is_from_me(message):
            return False
        
        pattern = f"\\b(hey|yes|yeah|no|nah|okay|thanks)?,?\\s*({self.nicknames})\\b"
        
        result = re.search(pattern, message['text'], re.IGNORECASE) is not None
    
        self.log.debug(f"is_activated={result}")

        return result

    def is_boredom_request(self, message):
        pattern = f"\\b(bored|tell me something|i'm board|i am board)"

        result = re.search(pattern, message['text'], re.IGNORECASE) is not None
    
        if result:
            self.log.debug(f"Message matches is_boredom_request")

        return result

    def is_discussion_continued(self, message):
        if self.is_from_me(message):
            return False
        
        return (
            self.is_talking_to_me(message) and
            self.time_since_last_message_or_tts() < 30 and
            not self.just_spoke(message)
        )
    
    def contains_link(self, message):
        pattern = "\\w+\\.\\w+\/\\w+"

        result = re.search(pattern, message['text']) is not None

        return result
    
    def is_likely_spam(self, message):
        if self.is_from_me(message) or self.is_from_streamer(message):
            return False

        matches = 0
        if self.contains_link(message):
            self.log.debug(f"Message contains link, spam matches={matches}")
            matches += 1

        pattern = '\\b(cheap|best|viewers|google)\\b'
        if re.search(pattern, message['text'], re.IGNORECASE) is not None:
            self.log.debug(f"Message contains spam language, spam matches={matches}")
            matches += 1

        if not message['text'].isascii():
            self.log.debug(f"Message contains unicode, spam matches={matches}")
            matches += 1

        return matches > 1

    def should_reply_activation(self, message):
        result = False

        if self.is_likely_spam(message):
            self.log.debug("Replying to likely spam")
            result = "Reply to someone sending spam."
        
        elif self.is_discussion_continued(message):
            self.log.debug("Replying to continued discussion")
            result = "Reply a continued converation."

        elif self.is_activated(message):
            self.log.debug("Replying to activation")

            if self.is_boredom_request(message):
                result = self.reply_boredom_ideas()
            else:
                result = "Reply to being addressed directly."

        elif self.time_since_last_interaction() > 900:
            self.log.debug("Replying to long interaction delay")
            result = self.reply_boredom_ideas()

        if result:
            # Reset last interaction time early so I don't end up with a race condition
            # while waiting for a response from chatgpt..
            self.last_interaction_time = time.time()
        
        return result

    def random_word(self, words=wordlist.words):
        word_idx = randint(0, len(words))
        return words[word_idx]
    
    def reply_boredom_ideas(self):
        idea = self.random_word(["a joke","a story","an anecdote","a fact","a poem","a song"])
        subject = self.random_word()

        return (f"Reply with {idea} about this subject: \"{subject}\". "
                f"Start your reply with \"Here's {idea} about {subject}.\"")

    def should_reply_legacy(self, message):
        result = (
            self.is_talking_to_me(message) or
            self.time_since_last_message() > 300 or
            self.is_in_conversation(message)
        ) and (
            not self.just_spoke(message) or
            self.is_randomly(0.1)
        ) and (
            # TODO Hack to make conversation mode less chatty, needs work
            # to be more conversational..  Revisit if using conversation mode.
            self.time_since_last_message() > 30
        )

        self.log.debug(f"should_reply={result}")

        return result

    def should_reply(self, message):
        if self.reply_mode == "activation":
            return self.should_reply_activation(message)
        else:
            return self.should_reply_legacy(message)


    def reply(self, context):
        self.log.info("Getting response")
        response = self.chatgpt.get_response(self.history, context)

        self.log.info(f"Responding with '{response}'")
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

        self.last_interaction_time = time.time()
        self.last_message_time = time.time()

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

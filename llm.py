import os
import re
import random
import time
from openai import OpenAI

from config import *
from logs import *


class LLMApp():
    def __init__(self):
        self.log = Logger("llm")
        self.name = CONFIG.get("chatbot", "name")
        self.streamer = CONFIG.get("streamer", "name")
        self.game = CONFIG.get("game", "name")
        self.template_data = self.get_template_data()
        self.template_path = CONFIG.get("vllm", "prompt_template")
        self.examples_path = CONFIG.get("vllm", "examples_path")
        self.base_url = CONFIG.get("vllm", "base_url")
        self.api = CONFIG.get("vllm", "api", fallback="chat")
        self.model = CONFIG.get("vllm", "model")
        self.code_model = CONFIG.get("vllm", "code_model", fallback=self.model)
        self.max_tokens = CONFIG.getint("vllm", "max_tokens", fallback=256)
        self.max_tokens_code = CONFIG.getint("vllm", "max_tokens_code", fallback=32767)
        self.max_tokens_boredom = CONFIG.getint("vllm", "max_tokens_boredom", fallback=512)
        self.reasoning_effort = CONFIG.get("vllm", "reasoning_effort", fallback="low")
        self.reasoning_effort_code = CONFIG.get("vllm", "reasoning_effort_code", fallback="medium")

        self.client = OpenAI(base_url=self.base_url, api_key="EMPTY")

    def get_template_data(self):
        data = {
            'bot_name': self.name,
            'streamer_name': self.streamer,
            'game': self.game
        }

        return data

    def get_random_files(self, path, count=1):
        if os.path.exists(path):
            files = os.listdir(path)
            filtered = [os.path.join(path, f) for f in files if re.search("^example\\d+.txt$", f)]
            random.shuffle(filtered)

            return filtered[0:count]
        else:
            return []

    def get_examples(self):
        example_count = CONFIG.getint("vllm", "examples_count", fallback=5)
        general_ratio = CONFIG.getfloat("vllm", "examples_general_to_game_ratio", fallback=0.5)
        general_count = int(example_count * general_ratio)

        general_examples = self.get_random_files(os.path.join(self.examples_path, "General"), example_count)
        game_examples = self.get_random_files(os.path.join(self.examples_path, self.game), example_count)

        examples = general_examples[0:general_count]
        examples.extend(game_examples[0:example_count - len(examples)])

        return examples

    def get_code_messages(self, fileinfo):
        if fileinfo is None:
            return []

        return [
            {
                "role":"user",
                "content":f"I'm currently editing `{fileinfo['path']}` as follows:"
            },
            {
                "role":"user",
                "content": ''.join([
                    f"```{fileinfo['language']}\n",
                    fileinfo['content'],
                    "\n```"
                ])
            }
        ]

    def get_history_times(self, history):
        clip_history = "```"
        now = time.time()

        for message in history:
            if 'sent' not in message:
                continue
            clip_history += f"t={int(now-message['sent'])} [{message['author']}] {message['text']}\n"

        clip_history += "```"
        return clip_history

    def get_history_messages(self, history, context):
        messages = [
            { "role":"user", "content":"The following is my recent chat history with time offsets:" },
            { "role":"user", "content":self.get_history_times(history) },
            { "role":"user", "content":context['message']['text'] },
        ]

        return messages

    def get_chat_messages(self, history, context):
        messages = []
        instruction_role = "developer" if self.api == "responses" else "system"

        ## Read the template and add the output as the first message
        template = open(self.template_path, "r", encoding="utf8").read()
        messages.append({ "role":instruction_role, "content": template.format(**self.template_data) })

        messages.append({ "role":instruction_role, "content":"Messages from the streamer and their audience are prefixed with their name in brackets." })
        messages.append({ "role":instruction_role, "content":"IMPORTANT: Messages from the bot are not prefixed." })

        ## If this is a code response, add info and contents of the file being edited
        ## Only include the last message from the user as the question
        if context['type'] == 'code':
            self.log.debug("Appending code request messages...")
            messages.extend(self.get_code_messages(context['active_file']))

            # Include just the last few messages in the history
            for message in history[-5:]:
                role = (message['author'] == self.name and "assistant") or "user"
                messages.append({ "role":role, "content":message['text'] })

        elif context['type'] == 'history':
            self.log.debug("Appending clip request messages...")
            messages.extend(self.get_history_messages(history, context))

        ## Otherwise, try to add to the entire conversation
        else:
            self.log.debug(f"Appending conversation request ({context['type']}) messages...")
            messages.append({ "role":instruction_role, "content":"Add to the following conversation"})

            for message in history:
                role = (message['author'] == self.name and "assistant") or "user"
                author = re.sub("[^A-Za-z0-9_\-]", "_", message['author'], flags=re.A)

                if role == "user":
                    messages.append({ "role":role, "content":f"[{author}] {message['text']}" })
                else:
                    messages.append({ "role":role, "content":message['text'] })

        ## Add the contextual prompt to the end
        messages.append({ "role":instruction_role, "content":context['prompt'] })

        for message in messages:
            self.log.debug(message)

        return messages

    def get_chat_response(self, history, context):
        """ Uses an OpenAI-compatible Chat Completions API to get a response """

        try:
            messages = self.get_chat_messages(history, context)

            model = self.model
            tokens = self.max_tokens

            if context['type'] == 'code' or context['type'] == 'clip':
                model = self.code_model
                tokens = self.max_tokens_code
            elif context['type'] == 'boredom':
                tokens = self.max_tokens_boredom

            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=tokens,
                temperature=0.8,
                frequency_penalty=0.5,
                presence_penalty=0.0,
                stop=[ f"{self.name}:", f"{self.streamer}:" ]
            )

            text = (response.choices[0].message.content or "").strip()

            if len(text) == 0:
                self.log.error("Got empty text response from Chat Completions API")
                self.log.error(response)
                return None

            return text

        except Exception as e:
            self.log.error(f"Chat Completions API failed: {e}")
            return None

    def get_responses_response(self, history, context):
        """ Uses an OpenAI-compatible Responses API to get a response """

        try:
            messages = self.get_chat_messages(history, context)

            model = self.model
            reasoning_effort = self.reasoning_effort

            if context['type'] == 'code' or context['type'] == 'clip':
                model = self.code_model
                reasoning_effort = self.reasoning_effort_code

            response = self.client.responses.create(
                model=model,
                input=messages,
                reasoning={ "effort": reasoning_effort }
            )

            text = response.output_text

            if len(text) == 0:
                self.log.error("Got empty text response from Responses API")
                self.log.error(response)
                return None

            return text

        except Exception as e:
            self.log.error(f"Responses API failed: {e}")
            return None

    def get_response(self, history, context):
        if self.api == "responses":
            return self.get_responses_response(history, context)
        elif self.api == "chat":
            return self.get_chat_response(history, context)
        else:
            self.log.error(f"Invalid llm api: {self.api}")
            return None


if __name__ == "__main__":
    import logging
    import sys
    import traceback

    # Attach a console handler so logs are visible (the default handler queues for curses)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)-5s [%(name)-12.12s] %(message)s"))
    root.addHandler(console)

    app = LLMApp()
    print(f"base_url      = {app.base_url}")
    print(f"api           = {app.api}")
    print(f"model         = {app.model}")
    print(f"template_path = {app.template_path}")
    print()

    # 1. Bare connectivity check - smallest possible Chat Completions call, no prompt machinery.
    # Always uses chat.completions regardless of configured api, since it's the universal baseline.
    print("--- Bare Chat Completions API call ---")
    try:
        resp = app.client.chat.completions.create(
            model=app.model,
            messages=[{ "role": "user", "content": "Say the word 'pong' and nothing else." }],
            max_tokens=16,
        )
        print(f"content: {resp.choices[0].message.content!r}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()

    # 2. Full path through get_response() — exercises whichever api is configured
    print(f"\n--- Full get_response() call (api={app.api}) ---")
    history = [
        { "author": app.streamer, "text": "Hello, can you hear me?", "sent": time.time() },
    ]
    context = {
        "type": "conversation",
        "prompt": f"Reply briefly as {app.name}.",
    }
    try:
        text = app.get_response(history, context)
        print(f"response: {text!r}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()

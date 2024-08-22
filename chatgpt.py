import os
import re
import random
import openai

from config import *
from logs import *

openai.api_key = SECRETS.get("openai.com", "token")

class CompletionApp():
    def __init__(self):
        self.log = Logger("chatgpt")
        self.name = CONFIG.get("chatbot", "name")
        self.streamer = CONFIG.get("streamer", "name")
        self.game = CONFIG.get("game", "name")
        self.template_data = self.get_template_data()
        self.template_path = CONFIG.get("openai.com", "prompt_template")
        self.examples_path = CONFIG.get("openai.com", "examples_path")
        self.api = CONFIG.get("openai.com", "api", fallback="completion")
        self.model = CONFIG.get("openai.com", "model", fallback="text-davinci-003")

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
        example_count = CONFIG.getint("openai.com", "examples_count", fallback=5)
        general_ratio = CONFIG.getfloat("openai.com", "examples_general_to_game_ratio", fallback=0.5)
        general_count = int(example_count * general_ratio)
        
        general_examples = self.get_random_files(os.path.join(self.examples_path, "General"), example_count)
        game_examples = self.get_random_files(os.path.join(self.examples_path, self.game), example_count)

        examples = general_examples[0:general_count]
        examples.extend(game_examples[0:example_count - len(examples)])

        return examples
                
    def get_completion_prompt(self, history_string):
        paths = [ CONFIG.get("openai.com", "prompt_template") ]
        paths.extend(self.get_examples())

        prompt = ""

        for path in paths:
            self.log.info(f"Adding {path} to prompt..")
            template = open(path, 'r').read()

            if len(prompt) > 0:
                prompt += f"\nExample conversation - {path}:\n\n"

            prompt += template.format(**self.template_data)
            prompt += "\n"

        prompt += "\nAdd to the conversation below:\n\n"
        prompt += history_string + f"\n{self.name}:"

        self.log.debug("--- START PROMPT ---")
        self.log.debug(prompt)
        self.log.debug("--- END PROMPT ---")

        return prompt

    def get_chat_messages(self, history):
        messages = []
        template_path = CONFIG.get("openai.com", "prompt_template")
        template = open(template_path, "r").read()

        messages.append({ "role":"system", "content": template.format(**self.template_data) })

        # examples = self.get_examples()

        # for example in examples:
        #     messages.append({ "role":"system", "content": f"Example conversation {example}"})
        #     with open(example, "r") as fil:
        #         for line in fil:
        #             line = line.format(**self.template_data)
        #             (author, content) = line.split(': ')
        #             #role = author == (self.name and "assistant") or "user" 
                    
        #             messages.append({ "role":"system", "content":content, "name": author })

        messages.append({ "role":"system", "content":"Add to the following conversation"})

        for message in history:
            role = (message['author'] == self.name and "assistant") or "user"
            messages.append({ "role":role, "content":message['text'], "name":message['author'] })

        self.log.info(messages)
        return messages
    
    def get_chat_response(self, history):
        messages = self.get_chat_messages(history)

        response = openai.ChatCompletion.create(
            model=self.model,
            messages=messages,
            temperature=0.5,
            max_tokens=120,
            frequency_penalty=0.5,
            presence_penalty=0.0,
            stop=[ f"{self.name}:", f"{self.streamer}:" ]
        )

        text = response['choices'][0]['message']['content'].strip()
        return text
    
    def get_completion_response(self, history_string):
        prompt = self.get_completion_prompt(history_string)

        response = openai.Completion.create(
            model=self.model,
            prompt=prompt,
            temperature=0.5,
            max_tokens=120,
            frequency_penalty=0.5,
            presence_penalty=0.0,
            stop=[ f"{self.name}:", f"{self.streamer}:" ]
        )

        self.log.info(response)
        text = response['choices'][0]['text'].strip()

        return text

    def history_string(self, history):
        return "\n".join([f"{i['author']}: {i['text']}" for i in history])

    def get_response(self, history):
        if self.api == "completion":
            history_string = self.history_string(history)
            return self.get_completion_response(history_string)
        
        elif self.api == "chat":
            return self.get_chat_response(history)
        
        else:
            self.log.error(f"Invalid chatgpt api: {self.api}")
            return None
        
if __name__ == "__main__":
    history_string = "potate_oh_no: Hello bot."
    chatgpt = CompletionApp()
    chatgpt.get_prompt(history_string)


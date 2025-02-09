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
        self.api = CONFIG.get("openai.com", "api", fallback="chat")
        self.model = CONFIG.get("openai.com", "model", fallback="gpt-4o")
        self.code_model = CONFIG.get("openai.com", "code_model", fallback=self.model)
        self.max_tokens = CONFIG.getint("openai.com", "max_tokens", fallback=120)
        self.max_tokens_code = CONFIG.getint("openai.com", "max_tokens_code", fallback=480)

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
                
    def get_completion_prompt(self, history_string, context):
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

        prompt += f"\n{context}\n"

        self.log.debug("--- START PROMPT ---")
        self.log.debug(prompt)
        self.log.debug("--- END PROMPT ---")

        return prompt

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

    def get_chat_messages(self, history, context):
        messages = []

        ## Read the template and add the output as the first message
        template_path = CONFIG.get("openai.com", "prompt_template")
        template = open(template_path, "r").read()
        messages.append({ "role":"developer", "content": template.format(**self.template_data) })

        ## If this is a code response, add info and contents of the file being edited
        ## Only include the last message from the user as the question
        if context['type'] == 'code':
            self.log.debug("Appending code request messages...")
            messages.extend(self.get_code_messages(context['active_file']))
            question = history[-1]
            messages.append({ "role":"user", "content":question['text'] })

        ## Otherwise, try to add to the entire conversation
        else:
            self.log.debug(f"Appending conversation request ({context['type']}) messages...")
            messages.append({ "role":"developer", "content":"Add to the following conversation"})

            for message in history:
                role = (message['author'] == self.name and "assistant") or "user"
                author = re.sub("[^A-Za-z0-9_\-]", "_", message['author'],flags=re.A)
                messages.append({ "role":role, "content":message['text'], "name":author })

        ## Add the contextual prompt to the end
        messages.append({ "role":"developer", "content":context['prompt'] })

        for message in messages:
            self.log.debug(message)

        return messages
    
    def get_chat_response(self, history, context):
        messages = self.get_chat_messages(history, context)
        model = self.model
        tokens = self.max_tokens

        if context['type'] == 'code':
            model = self.code_model
            tokens = self.max_tokens_code

        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=messages,
                temperature=0.8,
                max_tokens=tokens,
                frequency_penalty=0.5,
                presence_penalty=0.0,
                stop=[ f"{self.name}:", f"{self.streamer}:" ]
            )
        except Exception as e:
            self.log.error(f"OpenAI Chat API failed: {e}")
            self.log.error(messages)
            return None    

        text = response['choices'][0]['message']['content'].strip()
        return text
    
    def get_completion_response(self, history_string, context):
        prompt = self.get_completion_prompt(history_string, context)

        response = openai.Completion.create(
            model=self.model,
            prompt=prompt,
            temperature=1, #was 0.5, default is 1
            max_tokens=120,
            frequency_penalty=0.5,
            presence_penalty=0.0,
            stop=[ f"{self.name}:", f"{self.streamer}:" ]
        )

        #self.log.info(response)
        text = response['choices'][0]['text'].strip()

        if len(text) == 0:
            self.log.error("Got empty text response from OpenAI")
            self.log.error(response)

        return text

    def history_string(self, history):
        return "\n".join([f"{i['author']}: {i['text']}" for i in history])

    def get_response(self, history, context):
        if self.api == "completion":
            history_string = self.history_string(history)
            return self.get_completion_response(history_string, context)
        
        elif self.api == "chat":
            return self.get_chat_response(history, context)
        
        else:
            self.log.error(f"Invalid chatgpt api: {self.api}")
            return None
        
if __name__ == "__main__":
    history_string = "potate_oh_no: Hello bot."
    chatgpt = CompletionApp()
    chatgpt.get_prompt(history_string)


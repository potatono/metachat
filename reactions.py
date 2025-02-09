import json
import re
import os
import shutil
import time

from config import *
from logs import *
from obs import ObsApp

class ReactionsApp():
    token = None

    def __init__(self, on_say=None):
        self.log = Logger("reactions")
        self.history = []
        self.on_say = on_say

        self.clips_path = CONFIG.get("reactions", "clips_path")
        self.source_path = CONFIG.get("reactions", "source_path")
        self.source_name = CONFIG.get("reactions", "source_name")

        self.matches = json.loads(CONFIG.get("reactions", "matches"))
        self.obs = ObsApp()

        ## Used to disable the reaction scene item after some time has passed
        self.reset_reaction_on = None
    
    def ensure_connected(self):
        self.obs.ensure_connected()

    def shutdown(self):
        self.obs.shutdown()

    def on_voice(self, text):
        text = re.sub("[^\\w\\s]", "", text)

        self.reset_reaction()

        for match in self.matches:
            pattern = f"\\b{match[0]}\\b"
            if re.search(pattern, text, re.IGNORECASE) is not None:
                self.play_reaction(match[1])
                return
    
    ## Because we're not using a thread here it's a bit of a hack
    ## When speaking happens we check if we should reset and then
    ## set the scene item back to disabled.
    ##
    ## This prevents clips from running again when scene changes
    ## happen.
    def reset_reaction(self):
        if self.reset_reaction_on is not None:
            if time.time() > self.reset_reaction_on:
                self.reset_reaction_on = None
                self.toggle_reaction(toggle_to=False)

    def play_reaction(self, reaction):
        try:
            self.copy_reaction(reaction)
            self.toggle_reaction()
            self.reset_reaction_on = time.time() + 30
        except Exception as err:
            self.log.error(err)

    def copy_reaction(self, reaction):
        clip_path = os.path.join(self.clips_path, reaction) + ".mp4"
        self.log.debug(f"Copying {clip_path} to {self.source_path}...")
        shutil.copy(clip_path, self.source_path)

    def toggle_reaction(self, toggle_to=True):
        uuid = self.obs.get_current_scene_uuid()
        item_id = self.obs.get_scene_item_by_name(uuid, self.source_name)
        self.toggle_scene_item(uuid, item_id, toggle_to)
        
    def toggle_scene_item(self, uuid, item_id, toggle_to=True):
        self.obs.set_scene_item_enabled(uuid, item_id, False)
        
        if toggle_to:
            time.sleep(0.25) 
            self.obs.set_scene_item_enabled(uuid, item_id, True)


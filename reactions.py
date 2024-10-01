import json
import re
import os
import shutil
import time

from config import *
from logs import *

from obswebsocket import obsws, requests

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
        self.ws = None

        ## Used to disable the reaction scene item after some time has passed
        self.reset_reaction_on = None
    
    def ensure_connected(self):
        if not self.ws:
            self.ws = obsws("localhost", 4455, SECRETS.get("reactions", "websocket_secret"))
            self.ws.connect()

    def shutdown(self):
        self.ws.disconnect()
        self.ws = None

    def on_message(self, text):
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
        scene = self.ws.call(requests.GetCurrentProgramScene())
        uuid = scene.getSceneUuid()
        self.log.debug(f"Current scene uuid is {uuid}")
    
        items = self.ws.call(requests.GetSceneItemList(sceneUuid=uuid))
        for i in items.getSceneItems():
            if i['sourceName'] == self.source_name:
                itemId = i['sceneItemId']
                self.toggle_scene_item(uuid, itemId, toggle_to)
        
    def toggle_scene_item(self, uuid, itemId, toggle_to=True):
        self.log.debug("Disable scene item")
        self.ws.call(requests.SetSceneItemEnabled(sceneUuid=uuid, 
                                                  sceneItemId=itemId, 
                                                  sceneItemEnabled=False))
        
        if toggle_to:
            time.sleep(0.25)
 
            self.log.debug("Enable scene item")
            self.ws.call(requests.SetSceneItemEnabled(sceneUuid=uuid, 
                                                      sceneItemId=itemId, 
                                                      sceneItemEnabled=True))


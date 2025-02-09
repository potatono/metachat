
from config import *
from logs import *

from obswebsocket import obsws, requests

class ObsApp:
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(ObsApp, cls).__new__(cls)
    
        return cls.instance
    
    def __init__(self):
        self.log = Logger("obs")
        self.host = CONFIG.get("obs", "websocket_host", fallback="localhost")
        self.port = CONFIG.getint("obs", "websocket_port", fallback=4455)
        self.secret = SECRETS.get("obs", "websocket_secret")
        self.ws = None

    def on_event(self, message):
        self.log.debug(f"Got message {message}")

    def ensure_connected(self):
        if not self.ws:
            try:
                self.ws = obsws(self.host, self.port, self.secret)
                self.ws.connect()
                self.ws.register(self.on_event)
            except Exception as e:
                self.log.error(f"Failed to connect to OBS: {e}")
                self.ws = None

    def shutdown(self):
        if self.ws is None:
            return
        self.ws.disconnect()
        self.ws = None

    def get_current_scene(self):
        if self.ws is None:
            return None
        
        scene = self.ws.call(requests.GetCurrentProgramScene())
        return scene

    def get_current_scene_uuid(self):
        scene = self.get_current_scene()

        if scene is None:
            return None
        
        return scene.getSceneUuid()

    def get_current_scene_name(self):
        scene = self.get_current_scene()

        if scene is None:
            return None
        
        return scene.getSceneName()

    def get_scene_by_name(self, scene_name):
        if self.ws is None:
            return None
        
        scenes = self.ws.call(requests.GetSceneList())
        for s in scenes.getScenes():
            if s['sceneName'] == scene_name:
                return s['sceneUuid']
        
        return None

    def set_current_scene_name(self, scene_name):
        if self.ws is None:
            return None
        
        self.log.debug(f"Setting scene to {scene_name}")
        self.ws.call(requests.SetCurrentProgramScene(sceneName=scene_name))

    def get_scene_item_by_name(self, scene_uuid, item_name):
        if self.ws is None:
            return None
        
        items = self.ws.call(requests.GetSceneItemList(sceneUuid=scene_uuid))
        for i in items.getSceneItems():
            if i['sourceName'] == item_name:
                return i['sceneItemId']
        
        return None
    
    def set_scene_item_enabled(self, scene_uuid, item_id, enabled=True):
        if self.ws is None:
            return None
        
        self.ws.call(requests.SetSceneItemEnabled(sceneUuid=scene_uuid, 
                                                  sceneItemId=item_id, 
                                                  sceneItemEnabled=enabled))
        self.log.debug(f"Set OBS item {scene_uuid}/{item_id} enabled={enabled}")

    def save_clip(self):
        if self.ws is None:
            return None
        
        self.ws.call(requests.SaveReplayBuffer())
        self.log.debug(f"Saved clip")

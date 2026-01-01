import threading
from collections import deque
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import time

from logs import Logger

@dataclass
class Event:
    type: str
    data: Any
    timestamp: float
    source: Optional[str] = None

class EventBus:
    instance = None
    lock = threading.Lock()
    
    def __new__(cls):
        if cls.instance is None:
            with cls.lock:
                if cls.instance is None:
                    cls.instance = super().__new__(cls)
        return cls.instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self.initialized = True
        
        # Per-subscriber event queues
        self.queues = {}
        self.filters = {}
        self.callbacks = {}
        self.lock = threading.RLock()
        self.count = 0
        
        # Global event history (optional, for debugging)
        self.history = deque(maxlen=1000)

        self.log = Logger("eventbus")
    
    def create_subscriber(self, name: Optional[str] = None, event_types: List[str] = [], callback: Optional[callable] = None) -> str:
        """Create a new subscriber and return its ID"""
        with self.lock:
            subscriber_id = f"{name or 'subscriber'}_{self.count}"
            self.count += 1
            
            self.queues[subscriber_id] = deque(maxlen=100)  # Limit queue size
            self.filters[subscriber_id] = set(event_types)
            self.callbacks[subscriber_id] = callback
            
            self.log.info(f"Subscriber {subscriber_id} created with events {event_types}")
            return subscriber_id
    
    def remove_subscriber(self, subscriber_id: str):
        """Remove a subscriber"""
        with self.lock:
            self.queues.pop(subscriber_id, None)
            self.filters.pop(subscriber_id, None)
    
    def update_filter(self, subscriber_id: str, event_types: List[str]):
        """Update which event types a subscriber wants to receive"""
        with self.lock:
            if subscriber_id in self.filters:
                self.filters[subscriber_id] = set(event_types)
    
    def publish(self, event_type: str, data: Any = None, source: Optional[str] = None):
        """Publish an event to all interested subscribers"""
        event = Event(
            type=event_type,
            data=data,
            timestamp=time.time(),
            source=source
        )
        
        with self.lock:
            # Add to history
            self.history.append(event)
            
            # Distribute to subscriber queues
            for subscriber_id, event_filter in self.filters.items():
                # If no filter set, receive all events, otherwise check filter
                if not event_filter or event_type in event_filter:
                    # If a callback is set, call it directly
                    if subscriber_id in self.callbacks and self.callbacks[subscriber_id]:
                        self.log.debug(f"Dispatching event {event_type} to callback of {subscriber_id}")
                        self.callbacks[subscriber_id](event)
                        
                    # Otherwise add to the queue to be pulled later
                    elif subscriber_id in self.queues:
                        self.log.debug(f"Queueing event {event_type} for subscriber {subscriber_id}")
                        self.queues[subscriber_id].append(event)
                    
    
    def get_events(self, subscriber_id: str, max_events: int = 10) -> List[Event]:
        """Get pending events for a subscriber (non-blocking)"""
        with self.lock:
            if subscriber_id not in self.queues:
                return []
            
            queue = self.queues[subscriber_id]
            events = []
            
            for _ in range(min(max_events, len(queue))):
                if queue:
                    events.append(queue.popleft())
            self.log.debug(f"Returning {len(events)} events for subscriber {subscriber_id}")    
            return events
    
    def peek_events(self, subscriber_id: str, max_events: int = 10) -> List[Event]:
        """Peek at pending events without removing them"""
        with self.lock:
            if subscriber_id not in self.queues:
                return []
            
            queue = self.queues[subscriber_id]
            return list(queue)[:max_events]
    
    def get_queue_size(self, subscriber_id: str) -> int:
        """Get the number of pending events for a subscriber"""
        with self.lock:
            if subscriber_id not in self.queues:
                return 0
            return len(self.queues[subscriber_id])

# Convenience functions for global event bus
eventbus = EventBus()

def create_subscriber(name: Optional[str] = None, event_types: List[str] = []) -> str:
    return eventbus.create_subscriber(name, event_types)

def remove_subscriber(subscriber_id: str):
    eventbus.remove_subscriber(subscriber_id)

def update_filter(subscriber_id: str, event_types: List[str]):
    eventbus.update_filter(subscriber_id, event_types)

def publish(event_type: str, data: Any = None, source: Optional[str] = None):
    eventbus.publish(event_type, data, source)

def get_events(subscriber_id: str, max_events: int = 10) -> List[Event]:
    return eventbus.get_events(subscriber_id, max_events)

def peek_events(subscriber_id: str, max_events: int = 10) -> List[Event]:
    return eventbus.peek_events(subscriber_id, max_events)

def get_queue_size(subscriber_id: str) -> int:
    return eventbus.get_queue_size(subscriber_id)

# Event type constants
class Events:
    CHAT_MESSAGE = "chat_message"
    VOICE_INPUT = "voice_input"
    TEXT_INPUT = "text_input"
    USER_INPUT = "user_input"
    STREAMER_LINE = "streamer_line"
    STREAMER_PHRASE = "streamer_phrase"
    CHATBOT_RESPONSE = "chatbot_response"
    CONNECTION_STATUS = "connection_status"
    APPLICATION_SHUTDOWN = "application_shutdown"
    TICK = "tick"
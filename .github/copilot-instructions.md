# Metachat - AI Coding Agent Instructions

## System Architecture

Metachat is a streaming chatbot system with a **curses-based terminal interface** that integrates multiple real-time services through a central **event-driven architecture**.

### Core Components
- **Application** (`metachat.py`) - Main curses UI loop and event coordinator
- **EventBus** (`eventbus.py`) - Singleton pub/sub system with per-subscriber queues and callbacks
- **ChatApp** (`chat.py`) - Ingests messages from Restream, logs chat history
- **StreamerApp** (`streamer.py`) - Handles voice transcription (Rev.AI) and Twitch output
- **ChatbotApp** (`chatbot.py`) - OpenAI-powered responses with context and macro system
- **ReactionsApp** (`reactions.py`) - OBS integration for visual reactions

### Event System Patterns
Events flow through `eventbus.py` using predefined types in `Events` class:
```python
# Publishing pattern used throughout codebase
eventbus.publish(Events.CHAT_MESSAGE, data=message, source="restream")

# Subscription with callbacks in __init__ methods
eventbus.create_subscriber("chatbot", [Events.CHAT_MESSAGE], callback=self.on_event)
```

## Configuration System
- **config.ini** - All application settings with INI interpolation (`%(variable)s`)
- **secrets.ini** - OAuth tokens and API keys (use `secrets.ini.sample` as template)
- Access via: `CONFIG.get("section", "key", fallback=default)` or `SECRETS.get()`

### Key Configuration Patterns
- OAuth sections per service: `[chatbot.twitch.tv]`, `[streamer.twitch.tv]`, `[restream.io]`
- Feature toggles: `send_to_twitch`, `use_voice_input`, `enable_copilot_server`
- Path templates with date interpolation: `logs/chat-%(year)s-%(month)s-%(day)s.log`

## Development Workflows

### Running the Application
```bash
python metachat.py  # Starts curses interface
# ESC/Ctrl+C to quit, Enter to send messages
```

### Component Testing
Most components have `if __name__ == "__main__":` blocks for isolated testing:
```bash
python tts.py      # Test TTS directly
python avatar.py   # Test avatar rendering
python chatgpt.py  # Test OpenAI integration
```

### OAuth Token Setup
Run individual OAuth apps to get initial tokens:
```bash
python oauth.py    # Follow browser prompts for each service
```

## Integration Patterns

### Service Startup Sequence
Components use `ensure_connected()` pattern called from main loop:
1. Check if service is running
2. Obtain OAuth token if needed
3. Start service with token
4. Service publishes events to EventBus

### Thread Safety
- EventBus uses `threading.RLock()` for concurrent access
- Background services run in daemon threads
- Curses UI runs on main thread with `sleep(1/60.0)` game loop

### Logging System
Custom logger in `logs.py` with curses integration:
```python
self.log = Logger("component_name")  # Creates both file and curses output
```

## Chatbot AI System

### Context Management
ChatbotApp maintains conversation history with:
- Rolling window of recent messages (`history_size` config)
- Game-specific examples from `examples/` directory
- Dynamic context injection (clips, time, current game)

### Macro System
`macros.py` provides text templating with special syntax:
- `!clip` - OBS replay buffer integration
- `!time` - Current timestamp
- Custom macro definitions in config

### Response Modes
- `conversation` - Always responds to chat
- `activation` - Only responds when mentioned by nickname
- Configurable via `reply_mode` setting

## External Dependencies

### Required Services
- **Restream.io** - Chat aggregation across platforms
- **Rev.AI** - Real-time speech transcription 
- **OpenAI** - GPT completions for chatbot responses
- **Twitch** - Direct chat output (optional)
- **OBS** - Scene/source control and replay buffer

### Python Packages
Key dependencies: `azure-cognitiveservices-speech`, `pygame`, `openai`, `obs-websocket-py`, `rev_ai`, `websockets`

## File Organization
- Root level: Main application components and config
- `examples/` - Game-specific chatbot conversation examples
- `logs/` - Application and chat logs with date-based naming
- `public/` - Web assets for Copilot server integration
- `avatar/` - Avatar image assets for TTS visualization
- `viseme/` - Mouth position data for avatar lip-sync

## Common Gotchas
- OAuth tokens stored in `secrets.ini` need manual refresh when expired
- Curses interface requires proper terminal size - failures are silent
- Event subscribers must be created in `__init__` before service startup
- Config file uses INI interpolation - escape `%` chars as `%%`
- Avatar system requires Azure Speech Services for viseme data
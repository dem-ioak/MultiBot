import logging

class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        parts = []
        server = self.extra.get('server')
        user = self.extra.get('user')
        channel = self.extra.get('channel')

        if server:
            parts.append(f"[server={server}]")
        if user:
            parts.append(f"[user={user}]")
        if channel:
            parts.append(f"[channel={channel}]")

        prefix = " ".join(parts)
        if prefix:
            msg = f"{prefix} {msg}"

        return msg, kwargs

def setup_logging(log_file="app.log"):
    """Configure global logging. Call once at app startup."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),  # also log to console
        ],
    )

def get_logger(name: str, server=None, user=None, channel = None):
    """Get a logger with optional server/user context."""
    base_logger = logging.getLogger(name)
    return ContextAdapter(base_logger, {"server": server, "user": user, "channel" : channel})
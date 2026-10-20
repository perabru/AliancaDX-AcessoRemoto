"""Synthetic geometry and OS-input recorder, containing no generated secrets."""
MONITORS = [
    {"left": -1920, "top": -200, "width": 3840, "height": 1400},
    {"left": 0, "top": 0, "width": 1920, "height": 1080},
    {"left": -1920, "top": -200, "width": 1920, "height": 1200},
]


class RecordingInputs:
    def __init__(self):
        self.events, self.held_keys, self.held_buttons = [], set(), set()
    def move(self, x, y): self.events.append(("move", x, y))
    def key(self, key, down):
        self.events.append(("key", key, down))
        (self.held_keys.add if down else self.held_keys.discard)(key)
    def button(self, button, down):
        self.events.append(("button", button, down))
        (self.held_buttons.add if down else self.held_buttons.discard)(button)
    def wheel(self, delta): self.events.append(("wheel", delta))
    def release(self):
        self.events.append(("release",))
        self.held_keys.clear()
        self.held_buttons.clear()



"""Matplotlib widgets that avoid redundant synchronous whole-window paints."""
from matplotlib.widgets import TextBox


class ResponsiveTextBox(TextBox):
    def stop_typing(self):
        # TextBox calls this on outside clicks and tab changes even when it has
        # never had focus. Its base implementation forces canvas.draw() each
        # time, multiplying one button click into several full-window paints.
        if self.capturekeystrokes or self.cursor.get_visible():
            super().stop_typing()

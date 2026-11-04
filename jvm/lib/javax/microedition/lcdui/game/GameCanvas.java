package javax.microedition.lcdui.game;

import go1.Draw;
import go1.Events;
import javax.microedition.lcdui.Canvas;
import javax.microedition.lcdui.Graphics;
import javax.microedition.lcdui.Image;

/* Off-screen buffer and polled key states (MIDP 2). */
public abstract class GameCanvas extends Canvas {
    public static final int UP_PRESSED = 1 << Canvas.UP;
    public static final int DOWN_PRESSED = 1 << Canvas.DOWN;
    public static final int LEFT_PRESSED = 1 << Canvas.LEFT;
    public static final int RIGHT_PRESSED = 1 << Canvas.RIGHT;
    public static final int FIRE_PRESSED = 1 << Canvas.FIRE;
    public static final int GAME_A_PRESSED = 1 << Canvas.GAME_A;
    public static final int GAME_B_PRESSED = 1 << Canvas.GAME_B;
    public static final int GAME_C_PRESSED = 1 << Canvas.GAME_C;
    public static final int GAME_D_PRESSED = 1 << Canvas.GAME_D;

    private final Image buffer;
    private final boolean suppressKeys;
    private int keyStates, latched;

    protected GameCanvas(boolean suppressKeyEvents) {
        suppressKeys = suppressKeyEvents;
        buffer = Image.createImage(176, 220);
    }

    protected Graphics getGraphics() {
        return buffer.getGraphics();
    }

    public int getKeyStates() {
        int states = keyStates | latched;
        latched = 0;
        return states;
    }

    public void paint(Graphics g) {
        g.drawImage(buffer, 0, 0, Graphics.TOP | Graphics.LEFT);
    }

    public void flushGraphics() {
        flushGraphics(0, 0, getWidth(), getHeight());
    }

    public void flushGraphics(int x, int y, int w, int h) {
        if (!isShown() || w <= 0 || h <= 0) return;
        Draw.drawRegion(null, buffer, 0, 0, 176, getHeight(), 0, 0, 0, x, y, w, h);
        Draw.flush(x, y, w, h);
    }

    public void go1Key(int type, int code) {
        int action = 0;
        try {
            action = getGameAction(code);
        } catch (IllegalArgumentException e) {
            action = 0;
        }
        if (action != 0) {
            int bit = 1 << action;
            if (type == Events.KEY_PRESSED) { keyStates |= bit; latched |= bit; }
            else if (type == Events.KEY_RELEASED) keyStates &= ~bit;
        }
        if (!suppressKeys || action == 0) super.go1Key(type, code);
    }
}
